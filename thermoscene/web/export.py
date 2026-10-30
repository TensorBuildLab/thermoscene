"""Export a thermoscene USD stage to a self-contained, real-time thermal viewer (one HTML file).

    python -m thermoscene web case_studies/B_hvac_system/output/B_hvac_system.usda -o viewer.html

Geometry, temperature series, emissivities, provenance, ceiling-jet parameters and the scene's cameras are embedded;
the page recomputes the camera reading per pixel on the GPU with the same radiometry as the reference renderer.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from pxr import Usd, UsdGeom

from ..render.raycast import LEVELS, usd_faces
from ..render.shading import Series, cameras, labels, thermal_prims

TEMPLATE = Path(__file__).with_name("viewer.html")
MAX_STEPS = 4096          # longer series are subsampled for the viewer


def b64(a) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def export(usd_path, out_html, title=None) -> Path:
    stage = Usd.Stage.Open(str(usd_path))
    ser, labs = Series(stage), labels(stage)
    idx = np.arange(len(labs)) if len(labs) <= MAX_STEPS else np.linspace(0, len(labs) - 1, MAX_STEPS).astype(int)
    prims = thermal_prims(stage)
    world = stage.GetPrimAtPath("/World")
    spaces = [p for p in world.GetChildren() if p.HasAttribute("semantic:class")
              and p.GetAttribute("semantic:class").Get() in ("Zone", "Plenum", "Outdoors")]
    space_index = {str(p.GetPath()): i for i, p in enumerate(spaces)}
    sv = lambda p, a: [float(ser.get(p, a, int(t))) for t in idx]

    # geometry: non-indexed triangles + owning prim per vertex
    pos, owner = [], []
    for k, p in enumerate(prims):
        for f in usd_faces(p):
            for j in range(1, len(f) - 1):
                pos += [f[0], f[j], f[j + 1]]; owner += [k, k, k]
    pos = np.asarray(pos, np.float32); owner = np.asarray(owner, np.float32)

    temps = np.array([sv(p, "thermal:surfaceTemperature") for p in prims], np.float32)
    info = []
    for p in prims:
        path = str(p.GetPath())
        cls = p.GetAttribute("semantic:class").Get()
        st_ = p.GetAttribute("ts:surfaceType").Get() if p.HasAttribute("ts:surfaceType") else ""
        prov = p.GetAttribute("thermal:surfaceTemperature:provenance").Get()
        note = p.GetAttribute("thermal:surfaceTemperature:provenanceNote")
        size = p.GetAttribute("mep:size").Get() if p.HasAttribute("mep:size") else ""
        info.append(dict(name=p.GetName(), path=path, cls=st_ if cls in ("Surface", "Opening") else cls,
                         space=space_index.get("/".join(path.split("/")[:3]), 0),
                         eps=float(p.GetAttribute("thermal:emissivity").Get()),
                         prov=LEVELS.index(prov) if prov in LEVELS else len(LEVELS),
                         note=note.Get() if note else "", size=size or ""))
    pindex = {i["path"]: k for k, i in enumerate(info)}
    jets = []
    for k, p in enumerate(prims):
        rel = p.GetRelationship("thermal:ceilingJetTarget")
        if rel and rel.GetTargets() and str(rel.GetTargets()[0]) in pindex:
            jets.append(dict(diffuser=k, target=pindex[str(rel.GetTargets()[0])],
                             center=list(p.GetAttribute("thermal:ceilingJetCenter").Get()),
                             half=float(p.GetAttribute("thermal:ceilingJetHalfSize").Get()),
                             strength=sv(p, "thermal:ceilingJetStrength"), length=sv(p, "thermal:ceilingJetDecayLength")))
    sp = []
    for p in spaces:
        sp.append(dict(name=p.GetName(), cls=p.GetAttribute("semantic:class").Get(),
                       air=sv(p, "thermal:airTemperature"), refl=sv(p, "thermal:reflectedTemperature"),
                       bg=sv(p, "thermal:backgroundTemperature")))
    cams = []
    for c in cameras(stage):
        M = np.array(UsdGeom.Xformable(c).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
        cam = UsdGeom.Camera(c)
        hfov = 2 * np.degrees(np.arctan(cam.GetHorizontalApertureAttr().Get() / 2 / cam.GetFocalLengthAttr().Get()))
        env = c.GetRelationship("thermal:environment").GetTargets()
        cams.append(dict(name=c.GetName(), eye=M[3, :3].tolist(), target=(M[3, :3] - 3.0 * M[2, :3]).tolist(),
                         hfov=float(hfov), space=space_index.get(str(env[0]) if env else "", 0),
                         frames=[labs.index(f) for f in (c.GetAttribute("thermal:previewFrames").Get() or [])]))
    g = lambda n, d: (cameras(stage)[0].GetAttribute(n).Get() if cameras(stage) and cameras(stage)[0].HasAttribute(n) else d)
    meta = stage.GetRootLayer().customLayerData
    data = dict(
        title=title or meta.get("ts:case", Path(usd_path).stem), source=meta.get("ts:source", ""),
        labels=[labs[int(t)] for t in idx], step_minutes=int(meta.get("thermal:timestepMinutes", 15)) * int(len(labs) / len(idx)),
        prims=info, spaces=sp, jets=jets, cameras=cams, levels=LEVELS + ["background"],
        camera=dict(eps=float(g("thermal:camera:emissivity_setting", 0.95)), refl=float(g("thermal:camera:reflected_setting_c", 20.0)),
                    netd=float(g("thermal:camera:netd_k", 0.05))),
        band=list(stage.GetPrimAtPath(str(cameras(stage)[0].GetPath())).GetAttribute("thermal:band_um").Get()) if cameras(stage) else [8, 14],
        inferno=(np.array([colormaps["inferno"](i / 255)[:3] for i in range(256)]) * 255).round().astype(int).ravel().tolist(),
        n_prims=len(prims), n_steps=len(idx),
        positions=b64(pos), owner=b64(owner), temps=b64(temps))
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__THERMOSCENE_DATA__*/null", json.dumps(data, separators=(",", ":")))
    out = Path(out_html); out.write_text(encoding="utf-8", data=html)
    return out
