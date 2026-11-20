"""Tests of the embodied interface (thermoscene.embodied) and the internal-gain models.

Pure-python tests always run; tests that need a built case skip when it is missing.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thermoscene.embodied.dataset import rle, rle_decode  # noqa: E402
from thermoscene.embodied.sensor import ThermalCamera, agc8, dLdT  # noqa: E402
from thermoscene.embodied.world import value_noise  # noqa: E402
from thermoscene.generators.internal_gains import (ashrae55_clothing, clothing_temperature, occupant_presence,  # noqa: E402
                                                   skin_temperature)
from thermoscene.thermal import radiometry as rad  # noqa: E402

CASE_B = ROOT / "case_studies" / "B_hvac_system" / "output" / "B_hvac_system.usda"


# ---------------------------------------------------------------------------------------------- sensor model
def test_ideal_camera_inverts_radiance_exactly():
    cam = ThermalCamera.preset("ideal", emissivity_setting=1.0)
    t = np.linspace(-20, 80, 12).reshape(3, 4)
    cam_small = ThermalCamera({"resolution": [4, 3], "hfov_deg": 50, "netd_k": 0.0, "blur_px": 0.0,
                               "emissivity_setting": 1.0})
    assert np.abs(cam_small.measure(rad.radiance(t)) - t).max() < 0.01
    assert cam.blur_px == 0 and cam.ss == 1


def test_temporal_noise_matches_netd_and_fixed_pattern_stays():
    p = {"resolution": [200, 150], "hfov_deg": 50, "netd_k": 0.05, "blur_px": 0.0, "fpn_offset_k": 0.04,
         "emissivity_setting": 1.0}
    cam = ThermalCamera(p, seed=5)
    L = np.full((150, 200), rad.radiance(30.0))
    a, b = cam.measure(L, frame_seed=1), cam.measure(L, frame_seed=2)
    assert abs(np.std(a - b) / np.sqrt(2) - 0.05) < 0.005            # temporal noise = NETD at 30 degC
    fixed = (a + b) / 2 - 30.0
    assert abs(np.std(fixed) - np.hypot(0.04, 0.05 / np.sqrt(2))) < 0.006   # the fixed pattern persists across frames
    assert np.allclose(ThermalCamera(p, seed=5).offset_map, cam.offset_map)  # a unit is reproducible from its seed


def test_drift_grows_between_shutter_corrections_and_resets():
    cam = ThermalCamera({"resolution": [20, 10], "hfov_deg": 50, "netd_k": 0.0, "blur_px": 0.0, "drift_k_per_min": 0.3,
                         "drift_vignetting_share": 0.0, "ffc_interval_s": 120, "emissivity_setting": 1.0})
    L = np.full((10, 20), rad.radiance(25.0))
    t0 = cam.measure(L).mean()
    assert not cam.advance(60)
    t1 = cam.measure(L).mean()
    assert cam.advance(70)                                            # shutter at 120 s: drift resets
    t2 = cam.measure(L).mean()
    assert abs((t1 - t0) - 0.3) < 0.02 and abs(t2 - t0 - 0.3 * 10 / 60) < 0.02


def test_presets_load_and_netd_scales_with_scene_temperature():
    for name in ThermalCamera.presets():
        cam = ThermalCamera.preset(name)
        assert cam.w > 0 and cam.h > 0 and cam.blur_px >= 0
    assert dLdT(0.0) < dLdT(30.0) < dLdT(60.0)                        # the same radiance noise is more K when cold


def test_distortion_sample_map_stays_inside_the_rendered_image():
    cam = ThermalCamera.preset("lepton35")
    W, H, txr, tyr = cam.render_grid()
    col, row = cam.sample_map()
    assert txr >= cam.tx and col.min() >= -0.5 and col.max() <= W - 0.5 and row.min() >= -0.5 and row.max() <= H - 0.5


def test_agc_uses_the_full_range():
    g = agc8(np.random.default_rng(0).normal(25, 2, (60, 80)))
    assert g.dtype == np.uint8 and g.min() < 10 and g.max() > 245


# ---------------------------------------------------------------------------------------------- texture, labels
def test_value_noise_is_deterministic_smooth_and_unit_scale():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 20, (20000, 3))
    a, b = value_noise(p, 3, 0.25), value_noise(p, 3, 0.25)
    assert np.array_equal(a, b) and 0.8 < np.std(a) < 1.2 and abs(np.mean(a)) < 0.1
    near = value_noise(p + 0.01, 3, 0.25)
    assert np.corrcoef(a, near)[0, 1] > 0.95


def test_rle_roundtrip():
    rng = np.random.default_rng(1)
    for _ in range(5):
        m = rng.random((17, 23)) > 0.6
        assert np.array_equal(rle_decode(rle(m)), m)
    m = np.zeros((4, 4), bool); m[0, 0] = True
    assert rle(m)["counts"][0] == 0


# ---------------------------------------------------------------------------------------------- internal gains
def test_clothing_model_is_continuous_and_bounded():
    t = np.array([-5.0, 5.0, 26.0])
    lo, hi = ashrae55_clothing(t - 1e-6), ashrae55_clothing(t + 1e-6)
    assert np.allclose(lo, hi, atol=5e-3) and ashrae55_clothing(-30) == 1.0 and ashrae55_clothing(35) == 0.46


def test_clothing_surface_lies_between_air_and_skin_and_falls_with_insulation():
    ta = np.array([20.0, 24.0, 28.0])
    t05 = clothing_temperature(ta, ta, 70.0, 0.5)
    t10 = clothing_temperature(ta, ta, 70.0, 1.0)
    skin = skin_temperature(70.0)
    assert np.all(t05 > ta) and np.all(t05 < skin) and np.all(t10 < t05)
    assert 33.5 < skin < 34.0


def test_occupants_follow_the_simulated_count():
    count = np.array([0, 0.4, 0.6, 1.4, 2.6, 5.0])
    assert occupant_presence(count, 0, 3).tolist() == [False, False, True, True, True, True]
    assert occupant_presence(count, 2, 3).tolist() == [False, False, False, False, True, True]


# ---------------------------------------------------------------------------------------------- with a built case
def test_rasteriser_and_reference_camera_reproduce_the_reference_renderer():
    if not CASE_B.exists():
        print("skipped: case B not built"); return
    from thermoscene.embodied.world import ThermalWorld
    w = ThermalWorld(CASE_B)
    out = CASE_B.parent
    checked = 0
    for name, (pose, (tx, ty), (W, H), space) in w.camera_poses().items():
        seg_f = out / "renders" / name / "segmentation.npy"
        if not seg_f.exists():
            continue
        seg = np.load(seg_f)
        owner, _, _ = w.rasterize(pose, tx, ty, W, H)
        assert np.mean(owner == np.where(seg == w.np, -1, seg)) > 0.999, name
        for f in sorted((out / "renders" / name).glob("*_apparent.npy"))[:2]:
            tag = f.name[:-len("_apparent.npy")]
            tc = next(i for i, lab in enumerate(w.labels) if lab.replace(" ", "_").replace(":", "") == tag)
            cam = ThermalCamera.preset("reference", resolution=[W, H], hfov_deg=float(np.degrees(2 * np.arctan(tx))))
            fr = w.observe(pose, tc, cam, space=space, frame_seed=tc)
            ref = np.load(f)
            m = (fr.instance == np.where(seg == w.np, -1, seg)) & w.present[:, tc].all()
            if m.any():
                assert np.percentile(np.abs(fr.reading - ref)[m], 99) < 1e-3, (name, tag)
            assert fr.reading.shape == ref.shape
            checked += 1
    assert checked


def test_environment_api_and_fault_declaration():
    if not CASE_B.exists():
        print("skipped: case B not built"); return
    from thermoscene.embodied.env import ACTIONS, InspectionEnv
    from thermoscene.embodied.world import ThermalWorld
    w = ThermalWorld(CASE_B)
    cam = ThermalCamera.preset("lepton35", supersample=1)
    env = InspectionEnv(w, camera=cam, space="Zone", task="fault_search", max_steps=5)
    obs, info = env.reset(seed=0)
    assert obs["thermal"].shape == (cam.h, cam.w) and obs["pose"].shape == (5,) and obs["time"].shape == (4,)
    assert env.observation_space["thermal"].shape == (cam.h, cam.w)
    k = env.fault_ids[0]
    c = (w.prim_lo[k] + w.prim_hi[k]) / 2
    n = 1 if c[1] < 2.0 else -1                                          # look at the fault from inside the room
    eye = c + np.array([0, n * 2.0, 0.3])
    yaw = float(np.degrees(np.arctan2(c[1] - eye[1], c[0] - eye[0])))
    pitch = float(np.degrees(np.arctan2(c[2] - eye[2], np.linalg.norm((c - eye)[:2]))))
    env.reset(seed=0, options={"pose": [*eye, yaw, pitch], "time": "WINTER 07:00"})
    _, r, term, trunc, info = env.step(ACTIONS.index("declare"))
    assert info["new_found"] == [k] and r > 0.5 and term
    _, r2, *_ = env.step(ACTIONS.index("declare"))
    assert r2 < 0                                                         # nothing new: false alarm


def test_dataset_export_writes_coco_annotations():
    if not CASE_B.exists():
        print("skipped: case B not built"); return
    import json
    import tempfile
    from PIL import Image
    from thermoscene.embodied.dataset import export_dataset
    with tempfile.TemporaryDirectory() as tmp:
        export_dataset(CASE_B, tmp, n_views=2, camera="lepton35", seed=1, progress=False)
        ann = json.loads((Path(tmp) / "annotations.json").read_text(encoding="utf-8"))
        assert len(ann["images"]) >= 1 and ann["annotations"] and ann["categories"]
        img = np.asarray(Image.open(Path(tmp) / ann["images"][0]["file_name"]))
        assert img.dtype == np.uint16 and 20000 < np.median(img) < 40000         # centikelvin
        a = ann["annotations"][0]
        assert rle_decode(a["segmentation"]).sum() == a["area"]
