"""Reference CPU renderer: reads only the USD; the 'answer key' for any simulator's thermal camera."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom

from .shading import (LEVELS, Series, camera_environment, camera_rays, cameras, labels, provenance_frame, provenance_shares,
                      thermal_frame, thermal_prims)

log = logging.getLogger(__name__)


def fan(faces):
    tris = []
    for f in faces:
        f = np.asarray(f, float)
        tris += [f[[0, j, j + 1]] for j in range(1, len(f) - 1)]
    return np.array(tris)


def accel(face_lists):
    """[(index, triangles, bbox_min, bbox_max)] from a list of polygon-face lists."""
    out = []
    for k, faces in enumerate(face_lists):
        t = fan(faces)
        if len(t):
            p = t.reshape(-1, 3)
            out.append((k, t, p.min(0) - 1e-4, p.max(0) + 1e-4))
    return out


def raycast(origin, dirs, meshes, normals=False):
    n = len(dirs)
    best_t = np.full(n, np.inf); best = np.full(n, -1)
    nrm = np.zeros((n, 3)) if normals else None
    inv_d = 1.0 / np.where(np.abs(dirs) < 1e-12, 1e-12, dirs)
    for k, tris, lo, hi in meshes:
        t0, t1 = (lo - origin) * inv_d, (hi - origin) * inv_d
        tmin = np.minimum(t0, t1).max(1); tmax = np.maximum(t0, t1).min(1)
        sel = np.nonzero((tmax >= np.maximum(tmin, 0)) & (tmin < best_t))[0]
        if len(sel) == 0:
            continue
        d = dirs[sel]
        for a, b, c in tris:
            e1, e2 = b - a, c - a
            p = np.cross(d, e2); det = p @ e1
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
            s = origin - a
            u = (p @ s) * inv
            q = np.cross(s, e1)
            v = (d @ q) * inv
            t = (q @ e2) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6) & (t < best_t[sel])
            best_t[sel[hit]] = t[hit]; best[sel[hit]] = k
            if normals and hit.any():
                nn = np.cross(e1, e2); nrm[sel[hit]] = nn / (np.linalg.norm(nn) or 1.0)
    return (best, best_t, nrm) if normals else (best, best_t)


def provenance_map(prims, owner):
    """Per-pixel index into LEVELS of the model level of the surface seen (len(LEVELS) = background), without the
    per-frame effects (ceiling jets, stagnant fluid) that shading.provenance_frame adds."""
    lv = []
    for p in prims:
        a = p.GetAttribute("thermal:surfaceTemperature:provenance")
        lv.append(LEVELS.index(a.Get()) if a and a.Get() in LEVELS else len(LEVELS))
    return np.array(lv + [len(LEVELS)])[owner]


def usd_faces(prim):
    m = UsdGeom.Mesh(prim)
    pts = np.array(m.GetPointsAttr().Get(), float); idx = np.array(m.GetFaceVertexIndicesAttr().Get())
    faces, pos = [], 0
    for c in m.GetFaceVertexCountsAttr().Get():
        faces.append(pts[idx[pos:pos + c]]); pos += c
    return faces


def render_usd(usd_path, outdir, camera_names=None, frames=None, preview=True):
    stage = Usd.Stage.Open(str(usd_path))
    series, labs = Series(stage), labels(stage)
    prims = thermal_prims(stage)
    meshes = accel([usd_faces(p) for p in prims])
    results = {}
    for cam in cameras(stage):
        name = cam.GetName()
        if camera_names and name not in camera_names:
            continue
        o, d, (h, w) = camera_rays(cam)
        hit, t = raycast(o, d.reshape(-1, 3), meshes)
        owner = np.where(hit >= 0, hit, len(prims)).reshape(h, w)
        hits = o + d * np.where(np.isfinite(t), t, 0).reshape(h, w, 1)
        out = Path(outdir) / name; out.mkdir(parents=True, exist_ok=True)
        np.save(out / "segmentation.npy", owner)
        prov = provenance_map(prims, owner)
        np.save(out / "provenance_map.npy", prov)
        (out / "segmentation_labels.txt").write_text(encoding="utf-8", data="".join(f"{i}\t{p.GetPath()}\n" for i, p in enumerate(prims)))
        fl = frames or list(cam.GetAttribute("thermal:previewFrames").Get() or [])
        res, shares = {}, {}
        for lab in fl:
            tc = labs.index(lab)
            true_map, app = thermal_frame(stage, series, tc, prims, owner, hits, cam, seed=tc)
            tag = lab.replace(" ", "_").replace(":", "")
            np.save(out / f"{tag}_true.npy", true_map.astype(np.float32))
            np.save(out / f"{tag}_apparent.npy", app.astype(np.float32))
            pf = provenance_frame(stage, series, tc, prims, owner, hits)
            np.save(out / f"{tag}_provenance.npy", pf.astype(np.int8))
            shares[lab] = provenance_shares(pf)
            res[lab] = (tc, app)
        names = sorted({k for s in shares.values() for k in s}, key=(LEVELS + ["background"]).index)
        mean = {k: float(np.mean([s.get(k, 0.0) for s in shares.values()])) for k in names} if shares else {}
        (out / "provenance_share.json").write_text(encoding="utf-8", data=__import__("json").dumps(
            {"mean": mean, "frames": shares, "model_levels": provenance_shares(prov)}, indent=2))
        results[name] = (owner, res)
        if preview and res:
            _figure(stage, series, cam, prims, owner, res, Path(outdir) / f"preview_{name}.png")
    return results


def _figure(stage, series, cam, prims, owner, res, png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    env = camera_environment(stage, cam)
    n = len(res) + 1
    cols = min(n, 3); rows = int(np.ceil(n / cols))
    fig, axs = plt.subplots(rows, cols, figsize=(5 * cols, 3.9 * rows), squeeze=False)
    ax = axs[0, 0]
    ax.imshow(np.ma.masked_equal(owner, len(prims)), cmap="tab20", interpolation="nearest")
    for i, p in enumerate(prims):
        ys, xs = np.nonzero(owner == i)
        if len(xs) > owner.size * 0.006:
            ax.text(np.median(xs), np.median(ys), p.GetName().replace("_", " ")[:22], ha="center", va="center", fontsize=6,
                    bbox=dict(fc="white", alpha=0.75, lw=0, pad=0.8))
    ax.set_title(f"{cam.GetName()}: what the camera sees", fontsize=9); ax.axis("off")
    for k, (lab, (tc, app)) in enumerate(res.items(), start=1):
        ax = axs[k // cols, k % cols]
        lo, hi = np.percentile(app, 1), np.percentile(app, 99.5)
        im = ax.imshow(app, cmap="inferno", vmin=lo, vmax=hi)
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02, label="apparent °C")
        air = series.get(env, "thermal:airTemperature", tc)
        ax.set_title(f"{lab}   ({env.GetName()} air {air:.1f}°C)", fontsize=9); ax.axis("off")
    for k in range(n, rows * cols):
        axs[k // cols, k % cols].axis("off")
    fig.tight_layout(); fig.savefig(png, dpi=120); plt.close(fig)
