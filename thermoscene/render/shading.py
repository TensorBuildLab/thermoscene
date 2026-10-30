"""Per-pixel thermal shading, shared by the reference ray caster and the Isaac Sim script.

Inputs are renderer-agnostic: owner map (index of the prim seen by each pixel, len(prims) = nothing)
and world hit points. Everything else comes from the USD stage (time samples or the NPZ sidecar).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom

from ..thermal import radiometry as rad

TEMP = "thermal:surfaceTemperature"


def jet_delta(x, y, t_face, t_ceiling, strength, length, center, half):
    """Heuristic attached-jet temperature field around a 4-way ceiling diffuser."""
    dx, dy = np.asarray(x) - center[0], np.asarray(y) - center[1]
    r = np.maximum(np.maximum(np.abs(dx), np.abs(dy)) - half, 0.0)
    lobes = 0.6 + 0.4 * np.cos(2 * np.arctan2(dy, dx)) ** 2
    return (t_face - t_ceiling) * strength * np.exp(-r / max(length, 1e-3)) * lobes


class Series:
    """Read time-varying attributes from time samples or from the sidecar NPZ."""

    def __init__(self, stage):
        self.stage = stage
        meta = stage.GetRootLayer().customLayerData
        self.npz = None
        if meta.get("thermal:storage") == "sidecar":
            self.npz = np.load(Path(stage.GetRootLayer().realPath).parent / meta["thermal:sidecar"])

    def get(self, prim, name, tc):
        a = prim.GetAttribute(name)
        if self.npz is not None:
            key = a.GetCustomDataByKey("thermal:sidecarKey")
            if key:
                return float(self.npz[key.replace("/", "~")][tc])
        return a.Get(Usd.TimeCode(tc))


def labels(stage):
    return list(stage.GetRootLayer().customLayerData["thermal:timeCodeLabels"])


def thermal_prims(stage):
    return [p for p in Usd.PrimRange(stage.GetPrimAtPath("/World")) if p.IsA(UsdGeom.Mesh) and p.HasAttribute(TEMP)]


def cameras(stage):
    root = stage.GetPrimAtPath("/World/Cameras")
    return [p for p in root.GetChildren() if p.IsA(UsdGeom.Camera)] if root else []


def camera_environment(stage, cam_prim):
    t = cam_prim.GetRelationship("thermal:environment").GetTargets()
    return stage.GetPrimAtPath(t[0])


def camera_rays(cam_prim):
    cam = UsdGeom.Camera(cam_prim)
    w, h = cam_prim.GetAttribute("thermal:resolution").Get()
    ha, va, fl = cam.GetHorizontalApertureAttr().Get(), cam.GetVerticalApertureAttr().Get(), cam.GetFocalLengthAttr().Get()
    M = np.array(UsdGeom.Xformable(cam_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    jj, ii = np.meshgrid(np.arange(w), np.arange(h))
    x = ((jj + 0.5) / w * 2 - 1) * (ha / 2 / fl)
    y = (1 - (ii + 0.5) / h * 2) * (va / 2 / fl)
    d = np.stack([x, y, -np.ones_like(x)], -1).reshape(-1, 3) @ M[:3, :3]     # camera-space z = -1
    return M[3, :3], d.reshape(h, w, 3), (h, w)


def thermal_frame(stage, series: Series, tc, prims, owner, hits, cam_prim, seed=0):
    """(true surface temperature map, camera apparent temperature map), deg C."""
    env = camera_environment(stage, cam_prim)
    t_refl = series.get(env, "thermal:reflectedTemperature", tc)
    t_bg = series.get(env, "thermal:backgroundTemperature", tc)
    t_srf = np.array([series.get(p, TEMP, tc) for p in prims] + [t_bg])
    eps = np.array([p.GetAttribute("thermal:emissivity").Get() for p in prims] + [1.0])
    true_map = t_srf[owner].astype(float)
    index = {str(p.GetPath()): i for i, p in enumerate(prims)}
    for k, p in enumerate(prims):
        rel = p.GetRelationship("thermal:ceilingJetTarget")
        if not rel or not rel.GetTargets():
            continue
        j = index.get(str(rel.GetTargets()[0]))
        mask = owner == j
        if j is None or not mask.any():
            continue
        c = p.GetAttribute("thermal:ceilingJetCenter").Get()
        pts = hits[mask]
        true_map[mask] += jet_delta(pts[:, 0], pts[:, 1], t_srf[k], t_srf[j],
                                    series.get(p, "thermal:ceilingJetStrength", tc),
                                    series.get(p, "thermal:ceilingJetDecayLength", tc),
                                    (c[0], c[1]), p.GetAttribute("thermal:ceilingJetHalfSize").Get())
    g = lambda n, d: (cam_prim.GetAttribute(n).Get() if cam_prim.HasAttribute(n) else d)
    l_obs = rad.observed_radiance(true_map, eps[owner], t_refl)
    app = rad.apparent_temperature(l_obs, g("thermal:camera:emissivity_setting", 0.95), g("thermal:camera:reflected_setting_c", 20.0))
    app = rad.sensor_effects(app, g("thermal:camera:netd_k", 0.05), g("thermal:camera:blur_px", 0.7), seed=seed)
    return true_map, app
