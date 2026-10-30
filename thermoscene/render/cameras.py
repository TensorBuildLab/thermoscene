"""Camera poses: explicit (eye/target) or automatic (best view of a target, scored by visible pixels)."""
from __future__ import annotations

import logging
import re

import numpy as np
from pxr import Gf
from shapely.geometry import Point
from shapely.ops import unary_union

from ..ontology import Camera, Element, Provenance, Surface
from ..scene.geometry import plan_polygon

log = logging.getLogger(__name__)


def look_at(eye, target, up=(0, 0, 1)):
    eye, target, up = (np.array(v, float) for v in (eye, target, up))
    f = target - eye; f /= np.linalg.norm(f)
    if abs(f @ up) > 0.98:
        up = np.array([0.0, 1.0, 0.0])
    r = np.cross(f, up); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return Gf.Matrix4d(*r, 0, *u, 0, *(-f), 0, *eye, 1)


def rays_from(eye, target, hfov_deg, res):
    w, h = res
    M = np.array(look_at(eye, target))
    tx = np.tan(np.radians(hfov_deg) / 2); ty = tx * h / w
    jj, ii = np.meshgrid(np.arange(w), np.arange(h))
    d = np.stack([((jj + 0.5) / w * 2 - 1) * tx, (1 - (ii + 0.5) / h * 2) * ty, -np.ones((h, w))], -1)
    return np.asarray(eye, float), (d.reshape(-1, 3) @ M[:3, :3]).reshape(h, w, 3)


def resolve_frames(specs, labels):
    """Frame specs: exact label 'ENV MM-DD HH:MM' | 'ENV HH:MM' (last day of ENV). ENV is the run-period name; it may
    contain spaces and is matched case-insensitively (EnergyPlus reports environment names in upper case)."""
    out = []
    norm = lambda x: " ".join(str(x).split()).upper()
    by_norm = {norm(lab): lab for lab in labels}
    for s in specs or []:
        if norm(s) in by_norm:
            out.append(by_norm[norm(s)]); continue
        m = re.match(r"^(.+?)\s+(\d\d:\d\d)$", norm(s))
        if m:
            hits = [lab for lab in labels if re.match(rf"^{re.escape(m.group(1))} \d\d-\d\d {m.group(2)}$", norm(lab))]
            if hits:
                out.append(hits[-1]); continue
        raise KeyError(f"frame '{s}' not found (examples: {labels[:2]})")
    return out


def _space_id(spec, ids):
    return ids.get(spec, spec) if spec in ("zone", "plenum", "outdoors") else spec


def _target(scene, auto):
    want = auto.get("target")
    for t in list(scene.of(Element)) + list(scene.of(Surface)):
        if want in (t.cls, getattr(t, "surface_type", None), t.name, t.id):
            return t
    raise KeyError(f"auto camera: no target matching {want}")


def _center(faces):
    p = np.array([v for f in faces for v in f], float)
    return (p.min(0) + p.max(0)) / 2


def auto_pose(scene, ids, cam_cfg, meshes_fn, raycast_fn):
    auto = cam_cfg["auto"]
    space = _space_id(cam_cfg["space"], ids)
    tgt = _target(scene, auto)
    from ..usd.writer import faces_of
    tc = _center(faces_of(tgt))
    hfov = cam_cfg.get("hfov_deg", 62.0)
    meshes, order = meshes_fn()
    sp = scene.get(space)
    if sp.cls == "Outdoors":
        r = auto.get("distance", 7.0); hgt = auto.get("height", 3.0)
        cands = [tc + [r * np.cos(t), r * np.sin(t), hgt] for t in np.linspace(0, 2 * np.pi, 16, endpoint=False)]
    else:
        zone_like = [s for s in scene.of(Surface) if s.space in (space, ids["zone"]) and s.surface_type in ("Floor", "Ceiling")]
        plan = unary_union([plan_polygon(s.polygon) for s in zone_like]).buffer(-auto.get("inset", 0.3))
        z = sp.z_floor + auto.get("height", (sp.z_ceiling - sp.z_floor) / 2)     # height above the space floor
        xmin, ymin, xmax, ymax = plan.bounds
        cands = [np.array([x, y, z]) for x in np.linspace(xmin, xmax, 9) for y in np.linspace(ymin, ymax, 9)
                 if plan.contains(Point(x, y)) and np.linalg.norm([x - tc[0], y - tc[1]]) > auto.get("min_distance", 1.5)]
    best, best_score = None, -1
    k = order.index(tgt.id)
    for eye in cands:
        o, d = rays_from(eye, tc, hfov, (64, 48))
        hit, _ = raycast_fn(o, d.reshape(-1, 3), meshes)
        frac = float(np.mean(hit == k))
        cap = auto.get("max_fraction", 0.15)
        score = (frac if frac <= cap else cap - 0.7 * (frac - cap)) + 0.004 * len(set(hit[hit >= 0].tolist()))
        if frac > 0 and score > best_score:
            best, best_score = eye, score
    if best is None:
        raise RuntimeError(f"auto camera {cam_cfg['name']}: target {tgt.id} not visible from any candidate")
    return tuple(best), tuple(tc), space


def build_cameras(scene, ids, cfg, labels, meshes_fn, raycast_fn):
    for c in cfg.get("cameras", []):
        cc = dict(hfov_deg=cfg["camera"]["hfov_deg"], resolution=cfg["camera"]["resolution"]) | c
        if "auto" in c:
            eye, target, space = auto_pose(scene, ids, cc, meshes_fn, raycast_fn)
            lvl, note = Provenance.ASSUMED_LAYOUT, f"automatic viewpoint of {c['auto']['target']}"
        else:
            eye, target, space = tuple(c["eye"]), tuple(c["target"]), _space_id(c["space"], ids)
            lvl, note = Provenance.USER, "explicit pose"
        cam = scene.add(Camera(id=f"camera/{c['name']}", cls="Camera", name=c["name"], space=space, eye=eye, target=target,
                               hfov_deg=cc["hfov_deg"], resolution=tuple(cc["resolution"]),
                               frames=resolve_frames(c.get("frames", []), labels)))
        cam.prov("pose", lvl, note)
