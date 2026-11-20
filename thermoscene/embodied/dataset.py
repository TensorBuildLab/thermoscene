"""Export a labelled thermal-image dataset from a scene, in formats that vision and robotics tools read directly.

    python -m thermoscene dataset scene.usda -o dataset/ --views 200 --camera handheld_320

Layout of `out_dir`:
  images/000000.png            16-bit radiometric camera reading, value = round(100 * (T + 273.15)), i.e. centikelvin
                               (the "TLinear" convention of radiometric thermal cores)
  images_8bit/000000.png       8-bit image with plateau histogram equalisation (what a non-radiometric stream shows)
  labels/000000_true.png       16-bit true surface temperature (same encoding; 0 = background)
  labels/000000_instance.png   16-bit instance id (scene prim index + 1; 0 = background)
  labels/000000_category.png   8-bit category id (index into categories + 1; 0 = background)
  labels/000000_provenance.png 8-bit provenance level of the true temperature (index into provenance_levels)
  labels/000000_depth.png      16-bit distance along the optical axis in millimetres (0 = background)
  labels/000000_emissivity.png 8-bit effective emissivity x 250
  annotations.json             COCO-style: images (with time, intrinsics, extrinsics, camera unit), categories,
                               instance annotations (RLE masks, boxes, mean true temperature and reading, provenance,
                               fault flag); plus scene metadata
  datasheet.md                 what the data are, how they were made, and what they must not be used for

Views are sampled at random: a target object in the chosen spaces (faults and generated elements are preferred),
an eye position 1-6 m from it inside the space with an unobstructed line of sight, and a simulated time step.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from ..render.shading import LEVELS
from .sensor import ThermalCamera, agc8
from .world import Pose, ThermalWorld


def _png16(path, a):
    Image.fromarray(np.asarray(a, np.uint16)).save(path)


def _png8(path, a):
    Image.fromarray(np.asarray(a, np.uint8)).save(path)


def centikelvin(t):
    return np.where(np.isfinite(t), np.clip(np.round(100 * (np.asarray(t, float) + 273.15)), 1, 65535), 0).astype(np.uint16)


def rle(mask) -> dict:
    """COCO uncompressed run-length encoding (column-major, starting with a run of zeros)."""
    m = np.asarray(mask, np.uint8).ravel(order="F")
    change = np.flatnonzero(m[1:] != m[:-1]) + 1
    runs = np.diff(np.r_[0, change, len(m)]).tolist()
    if len(m) and m[0] == 1:
        runs = [0] + runs
    return {"size": [int(mask.shape[0]), int(mask.shape[1])], "counts": [int(c) for c in runs]}


def rle_decode(r) -> np.ndarray:
    h, w = r["size"]
    v, out = 0, []
    for c in r["counts"]:
        out.append(np.full(c, v, np.uint8)); v = 1 - v
    return np.concatenate(out)[:h * w].reshape((h, w), order="F")


def sample_view(world: ThermalWorld, rng, spaces, prefer_faults=0.3, dist=(1.0, 6.0), tries=60):
    """(Pose, space, target prim) with an unobstructed view of a target in one of `spaces`."""
    cand = [k for k in range(world.np) if world.space_of[k] in spaces]
    faults = [k for k in cand if world.fault[k]]
    elems = [k for k in cand if world.sem[k] not in ("Surface", "Opening")]
    for _ in range(tries):
        r = rng.random()
        pool = faults if (faults and r < prefer_faults) else (elems if elems and r < 0.75 else cand)
        k = int(pool[rng.integers(len(pool))])
        lo, hi = world.prim_lo[k], world.prim_hi[k]
        tgt = rng.uniform(lo, hi)
        sp = world.space_of[k]
        slo, shi = world.space_bounds.get(sp, (tgt - 8, tgt + 8))
        for _ in range(20):
            d = rng.uniform(*dist)
            u = rng.normal(size=3); u[2] *= 0.4; u /= np.linalg.norm(u)
            eye = tgt + d * u
            if sp in world.space_bounds:
                eye = np.clip(eye, slo + 0.2, shi - 0.2)
            if world.space_at(eye) != sp or np.linalg.norm(eye - tgt) < 0.5:
                continue
            dirn = (tgt - eye) / np.linalg.norm(tgt - eye)
            hit, _ = world.trace(eye[None], dirn[None])
            if hit[0] == k:
                return Pose.look_at(eye, tgt), sp, k
    return None


def intrinsics(cam: ThermalCamera) -> dict:
    fx = cam.w / (2 * cam.tx); fy = cam.h / (2 * cam.ty)
    return dict(fx=fx, fy=fy, cx=cam.w / 2, cy=cam.h / 2, width=cam.w, height=cam.h,
                distortion=dict(model="brown_radial_normalised_by_tan_half_hfov", k1=cam.k1, k2=cam.k2))


def export_dataset(world: ThermalWorld | str, out_dir, n_views: int = 100, camera: str = "handheld_320",
                   randomize: bool = True, seed: int = 0, spaces=("Zone", "Plenum", "Outdoors"), times=None,
                   realism: dict | None = None, progress: bool = True) -> Path:
    world = world if isinstance(world, ThermalWorld) else ThermalWorld(world)
    out = Path(out_dir)
    for d in ("images", "images_8bit", "labels"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    sp_paths = [world.space_path(s) for s in spaces if any(v["cls"] == s.capitalize() for v in world.spaces.values())]
    tidx = list(range(world.n)) if times is None else [world.time_index(t) if isinstance(t, str) else int(t) for t in times]
    base = ThermalCamera.preset(camera)
    cats = [dict(id=i + 1, name=c, supercategory=_super(c)) for i, c in enumerate(world.categories)]
    images, anns, prov_count, cat_count = [], [], Counter(), Counter()
    aid = 1
    for i in range(n_views):
        v = sample_view(world, rng, sp_paths)
        if v is None:
            continue
        pose, sp, target = v
        cam = base.randomized(rng) if randomize else base
        tc = int(tidx[rng.integers(len(tidx))])
        f = world.observe(pose, tc, cam, space=sp, realism=realism, rng=rng, texture_seed=int(rng.integers(1 << 20)))
        name = f"{i:06d}.png"
        _png16(out / "images" / name, centikelvin(f.reading))
        _png8(out / "images_8bit" / name, agc8(f.reading))
        stem = out / "labels" / name[:-4]
        _png16(f"{stem}_true.png", centikelvin(f.true))
        _png16(f"{stem}_instance.png", f.instance + 1)
        _png8(f"{stem}_category.png", f.category + 1)
        _png8(f"{stem}_provenance.png", f.provenance)
        _png16(f"{stem}_depth.png", np.where(np.isfinite(f.depth), np.clip(np.round(f.depth * 1000), 0, 65535), 0))
        _png8(f"{stem}_emissivity.png", np.clip(np.round(f.emissivity * 250), 0, 255))
        images.append(dict(id=i, file_name=f"images/{name}", width=cam.w, height=cam.h, time_label=f.time_label,
                           time_index=f.time_index, space=sp.split("/")[-1], target=world.prim_paths[target],
                           intrinsics=intrinsics(cam), extrinsics=dict(eye=pose.eye.round(4).tolist(),
                                                                       rotation_rows_right_up_back=pose.R.round(6).tolist()),
                           camera=cam.describe()))
        for k in np.unique(f.instance[f.instance >= 0]):
            m = f.instance == k
            ys, xs = np.nonzero(m)
            c = int(world.cat[k])
            anns.append(dict(id=aid, image_id=i, category_id=c + 1 if c >= 0 else 0, iscrowd=0, area=int(m.sum()),
                             bbox=[int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)],
                             segmentation=rle(m), instance=world.prim_paths[k], is_fault=bool(world.fault[k]),
                             true_temperature_mean=round(float(np.nanmean(f.true[m])), 3),
                             reading_mean=round(float(np.mean(f.reading[m])), 3),
                             provenance=LEVELS[int(np.bincount(f.provenance[m]).argmax())]
                             if np.bincount(f.provenance[m]).argmax() < len(LEVELS) else "background"))
            cat_count[world.categories[c] if c >= 0 else "?"] += 1
            aid += 1
        prov_count.update({(LEVELS + ["background"])[int(lv)]: int(n) for lv, n in zip(*np.unique(f.provenance, return_counts=True))})
        if progress and (i + 1) % 10 == 0:
            print(f"  {i + 1}/{n_views} views")
    meta = dict(source=world.stage.GetRootLayer().customLayerData.get("ts:source", ""), scene=Path(world.usd_path).name,
                time_step_minutes=world.step_minutes, camera_preset=camera, randomized=randomize, seed=seed,
                realism=world.realism if realism is None else realism, provenance_levels=LEVELS + ["background"],
                temperature_encoding="uint16 centikelvin: T_degC = value / 100 - 273.15; 0 = no value")
    (out / "annotations.json").write_text(json.dumps(dict(info=meta, images=images, categories=cats, annotations=anns)),
                                          encoding="utf-8")
    total = sum(prov_count.values()) or 1
    lines = [f"# Datasheet: {Path(world.usd_path).stem} thermal views", "",
             "**Motivation.** Labelled thermal images of a building's envelope, HVAC system and occupied spaces for "
             "training and testing perception and inspection agents, where field thermograms have no ground truth.", "",
             f"**Composition.** {len(images)} views ({camera}, {'randomised camera units' if randomize else 'one camera unit'}), "
             f"{len(anns)} instance annotations in {len(cat_count)} categories. Every pixel has a camera reading, the modelled "
             "true temperature, instance, category, provenance, depth and emissivity.", "",
             "Provenance of the true temperature over all pixels: " +
             ", ".join(f"{k} {100 * v / total:.1f} %" for k, v in prov_count.most_common()) + ".", "",
             f"**Collection.** Generated by thermoscene from {meta['source']}. Temperatures of surfaces come from "
             "EnergyPlus; those of generated elements from the models and rules recorded per instance in the scene "
             "(provenance labels). The camera model and scene realism parameters are in annotations.json.", "",
             "**Uses and limits.** Suitable for pre-training, for controlled studies (the truth is known) and for testing "
             "inspection policies. The images are not field thermograms: geometry of generated elements is rule-based, "
             "heuristic temperatures are uncalibrated, and the labels carry the modelled temperature, not unmodelled "
             "variation. Do not use the data as evidence about a real building.", ""]
    (out / "datasheet.md").write_text("\n".join(lines), encoding="utf-8")
    return out


def _super(c):
    if c in ("Wall", "Floor", "Ceiling", "Roof", "Window", "Door"):
        return "envelope"
    if c in ("Defect", "InsulationDefect"):
        return "fault"
    if c in ("Occupant", "Luminaire", "PlugLoad", "Furniture"):
        return "internal_gain"
    return "hvac"
