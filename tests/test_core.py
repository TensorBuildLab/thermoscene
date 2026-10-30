"""Tests. Pure-python tests always run; tests that need EnergyPlus or built case outputs skip otherwise.
Run:  python -m pytest -q tests      (or: python tests/test_core.py)
"""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thermoscene.energyplus.model import newell_normal  # noqa: E402
from thermoscene.scene.geometry import grid_split, triangulate  # noqa: E402
from thermoscene.thermal import radiometry as rad  # noqa: E402

CASES = ["A_single_room", "B_hvac_system", "C_doe_medium_office"]


def area(tris):
    return sum(0.5 * np.linalg.norm(np.cross(np.subtract(b, a), np.subtract(c, a))) for a, b, c in tris)


def test_triangulation_with_hole_keeps_area_and_orientation():
    wall = [(0, 0, 3), (0, 0, 0), (5, 0, 0), (5, 0, 3)]
    win = [(1, 0, 2.2), (1, 0, 0.9), (3, 0, 0.9), (3, 0, 2.2)]
    t = triangulate(wall, [win])
    assert abs(area(t) - (15 - 2.6)) < 1e-9
    assert np.allclose(newell_normal(t[0]), [0, -1, 0])


def test_grid_split_preserves_orientation():
    up = grid_split([(0, 0, 3), (5, 0, 3), (5, 4, 3), (0, 4, 3)], [], 0.5)
    down = grid_split([(0, 0, 0), (0, 4, 0), (5, 4, 0), (5, 0, 0)], [], 0.5)
    assert newell_normal(up[0])[2] > 0 and newell_normal(down[0])[2] < 0 and abs(area(up) - 20) < 1e-9


def test_radiometry_roundtrip_and_low_emissivity_misreading():
    assert abs(rad.apparent_temperature(rad.observed_radiance(25.0, 0.95, 20.0)) - 25.0) < 0.01
    metal = rad.apparent_temperature(rad.observed_radiance(35.0, 0.25, 24.0))
    assert 26 < metal < 29           # a 35 C galvanised duct reads ~27 C on a default-settings camera


def _built(case):
    return (ROOT / "case_studies" / case / "output" / f"{case}.usda").exists()


def test_isaac_shading_path_matches_reference_renderer():
    """Feed the reference segmentation and depth through the Isaac script's reconstruction; outputs must match."""
    from pxr import Usd
    from thermoscene.render.raycast import accel, raycast, usd_faces
    from thermoscene.render.shading import Series, camera_rays, cameras, labels, thermal_frame, thermal_prims
    checked = 0
    for case in CASES:
        if not _built(case):
            continue
        out = ROOT / "case_studies" / case / "output"
        st = Usd.Stage.Open(str(out / f"{case}.usda"))
        prims, ser, labs = thermal_prims(st), Series(st), labels(st)
        meshes = accel([usd_faces(p) for p in prims])
        for cam in cameras(st):
            frames = list(cam.GetAttribute("thermal:previewFrames").Get() or [])
            ref = out / "renders" / cam.GetName() / f"{frames[-1].replace(' ', '_').replace(':', '')}_apparent.npy"
            if not ref.exists():
                continue
            o, d, (h, w) = camera_rays(cam)
            hit, t = raycast(o, d.reshape(-1, 3), meshes)
            depth = np.where(np.isfinite(t), t, 0.0).reshape(h, w)          # = Isaac distance_to_image_plane
            owner = np.where(hit >= 0, hit, len(prims)).reshape(h, w)
            tc = labs.index(frames[-1])
            _, app = thermal_frame(st, ser, tc, prims, owner, o + d * depth[..., None], cam, seed=tc)
            assert np.abs(app - np.load(ref)).max() < 1e-4, (case, cam.GetName())
            checked += 1
    if not checked:
        print("skipped: no built cases")


def test_case_outputs_have_no_missing_temperatures():
    from pxr import Usd
    from thermoscene.render.shading import Series, labels, thermal_prims
    for case in CASES:
        if not _built(case):
            continue
        st = Usd.Stage.Open(str(ROOT / "case_studies" / case / "output" / f"{case}.usda"))
        ser, n = Series(st), len(labels(st))
        for p in thermal_prims(st):
            v = [ser.get(p, "thermal:surfaceTemperature", tc) for tc in (0, n // 2, n - 1)]
            assert all(np.isfinite(v)) and all(-60 < x < 120 for x in v), (case, p.GetPath(), v)


def test_topology_on_case_b():
    if not os.environ.get("THERMOSCENE_ENERGYPLUS") or not _built("B_hvac_system"):
        return
    from thermoscene.energyplus.model import EPModel
    from thermoscene.energyplus.topology import HVACGraph
    ep = next((ROOT / "case_studies/B_hvac_system/output/_work/post").glob("*.epJSON"))
    g = HVACGraph(EPModel(ep))
    classes = {c.cls for c, _ in g.serving("Office")["components"]}
    assert {"VAVTerminalUnit", "HotWaterCoil", "Fan", "CoolingCoil", "OutdoorAirMixer", "Chiller", "Boiler", "Pump"} <= classes
    coil = next(c for c in g.comps.values() if c.cls == "CoolingCoil")
    assert [s.cls for s in g.plant_sources(coil)] == ["Chiller"]


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("ok", name)
