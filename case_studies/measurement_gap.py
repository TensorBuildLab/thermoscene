"""How far does what a thermal camera reads depart from the true thermal state, and what governs the departure?

Three experiments on the built scenes of cases B (Phoenix) and C (Chicago), all with the embodied renderer:

A. Pixel gap. For every camera of B and C at its preview frames, the per-pixel difference between the reading and the
   modelled true surface temperature, split into the part due to the physics of measurement (emissivity, directional
   emissivity, specular and diffuse reflections, within-surface variation: an ideal sensor in a realistic scene) and
   the part added by the sensor (a 320 x 240 inspection-camera class, random units).
B. Contrast fidelity. For pairs a diagnostician compares (fault vs host, valve vs pipe, diffuser vs ceiling, ...),
   the true contrast, the contrast an ideal sensor reads and the contrast realistic camera units read, from a pose
   facing the pair.
C. Detectability vs distance. For the envelope void (inside, B; outside, C) and the duct-insulation gap (B), the
   contrast-to-clutter ratio of the fault in the reading, CCR = |C| / sigma (C: contrast of the fault's N pixels to a
   ring of host pixels around it; sigma: robust spatial standard deviation of the reading over that ring, which holds noise,
   fixed pattern and within-surface variation), for three camera classes and random units. A fault is taken as
   detectable when it covers at least N_MIN pixels and CCR >= K_CCR (both stated assumptions; the ring variation is
   spatially correlated, so a white-noise criterion such as Rose's |C| sqrt(N) / sigma would overstate detectability
   of large faults). Also reported for a clean image (no sensor effects, no within-surface variation), where sigma
   vanishes and any contrast becomes 'detectable': the shortcut of noise-free synthetic images.

Writes case_studies/measurement_gap.json and docs/measurement_gap.md.  Run:  python case_studies/measurement_gap.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene.embodied import ThermalCamera, ThermalWorld  # noqa: E402

PRESETS = ["lepton35", "handheld_320", "drone_640"]
UNITS = 8                      # random camera units per preset and view
K_CCR = 3.0                    # detectable: contrast >= 3 x the spatial standard deviation of the surroundings
N_MIN = 4                      # ... over at least 2 x 2 pixels
CLEAN = dict(angular_emissivity=False, specular_reflections=False, texture=dict(enabled=False), atmosphere_extinction_per_m=0.0)
LOW_E = {"fsk_foil", "galv_bare", "alu_jacket", "brass_valve", "copper"}


def scene(case):
    return ThermalWorld(ROOT / "case_studies" / case / "output" / f"{case}.usda")


def ideal_like(preset, **over):
    p = ThermalCamera.preset(preset)
    return ThermalCamera.preset("ideal", **(dict(resolution=[p.w, p.h], hfov_deg=p.hfov, supersample=p.ss) | over))


def ring_masks(f, k, host, r_out=None):
    """Fault pixels F and a ring R of host pixels 3 to r_out pixels away (r_out = max(6, sqrt(N)) for N fault pixels),
    without mixed (edge) pixels, so the ring measures the variation of the surroundings, not their edges."""
    F = f.instance == k
    n = int(F.sum())
    if n == 0:
        return F, None
    d = distance_transform_edt(~F)
    r_out = r_out or max(6.0, np.sqrt(n))
    R = (f.instance == host) & (d > 2) & (d <= r_out) & ~f.mixed
    return F, R if R.sum() >= 10 else None


def stats(f, k, host, field="reading"):
    """Contrast (median of the fault minus median of the ring) and robust spatial variation of the ring
    (1.4826 x median absolute deviation; uncorrected dead pixels are outliers a detector would reject)."""
    F, R = ring_masks(f, k, host)
    if R is None or not F.any():
        return None
    a = getattr(f, field)
    r = a[R][np.isfinite(a[R])]
    c = float(np.nanmedian(a[F]) - np.median(r))
    s = float(1.4826 * np.median(np.abs(r - np.median(r))))
    return dict(contrast=c, sigma=s, n=int(F.sum()), ccr=abs(c) / max(s, 1e-9),
                detect=bool(F.sum() >= N_MIN and abs(c) / max(s, 1e-9) >= K_CCR))


# ------------------------------------------------------------------------------------------------- A. pixel gap
def pixel_gap(rng):
    rows, err_phys, err_total, groups = [], [], [], {"low-emissivity metal": ([], []), "glass": ([], []),
                                                       "high-emissivity": ([], [])}
    for case in ("B_hvac_system", "C_doe_medium_office"):
        w = scene(case)
        for name, (pose, (tx, ty), (W, H), space) in w.camera_poses().items():
            cam_prim = next(c for c in w.stage.GetPrimAtPath("/World/Cameras").GetChildren() if c.GetName() == name)
            hfov = float(np.degrees(2 * np.arctan(tx)))
            for lab in cam_prim.GetAttribute("thermal:previewFrames").Get():
                tc = w.labels.index(lab)
                ideal = w.observe(pose, tc, ideal_like("handheld_320", hfov_deg=hfov), space=space)
                hit = (ideal.instance >= 0) & np.isfinite(ideal.true)
                e1 = (ideal.reading - ideal.true)[hit]
                group_of = lambda inst: (lambda m: np.where(np.isin(m, list(LOW_E)), "low-emissivity metal",
                                                            np.where(m == "glazing", "glass", "high-emissivity")))(
                    np.array([w.material[k] for k in inst]))
                grp1 = group_of(ideal.instance[hit])
                e2, grp2 = [], []
                for u in range(2):        # the camera's own pixels (its lens distortion moves the scene in the image)
                    cam = ThermalCamera.preset("handheld_320", hfov_deg=hfov).randomized(rng)  # same framing as the scene camera
                    f = w.observe(pose, tc, cam, space=space, rng=rng)
                    hit2 = (f.instance >= 0) & np.isfinite(f.true)
                    e2.append((f.reading - f.true)[hit2]); grp2.append(group_of(f.instance[hit2]))
                e2, grp2 = np.concatenate(e2), np.concatenate(grp2)
                err_phys.append(e1); err_total.append(e2)
                for g in groups:
                    groups[g][0].append(e1[grp1 == g]); groups[g][1].append(e2[grp2 == g])
                rows.append(dict(case=case[0], camera=name, frame=lab, pixels=int(hit.sum()),
                                 physics_over_1K=round(100 * float(np.mean(np.abs(e1) > 1)), 1),
                                 total_over_1K=round(100 * float(np.mean(np.abs(e2) > 1)), 1),
                                 total_over_2K=round(100 * float(np.mean(np.abs(e2) > 2)), 1),
                                 median_abs_total=round(float(np.median(np.abs(e2))), 2)))
    ep, et = np.concatenate(err_phys), np.concatenate(err_total)
    summ = lambda e: dict(pixels=int(e.size), median_abs_K=round(float(np.median(np.abs(e))), 2),
                          p95_abs_K=round(float(np.percentile(np.abs(e), 95)), 2),
                          share_over_1K=round(100 * float(np.mean(np.abs(e) > 1)), 1),
                          share_over_2K=round(100 * float(np.mean(np.abs(e) > 2)), 1))
    by_group = {g: dict(ideal_sensor=summ(np.concatenate(a)) if sum(x.size for x in a) else None,
                        realistic_camera=summ(np.concatenate(b)) if sum(x.size for x in b) else None)
                for g, (a, b) in groups.items()}
    return dict(views=rows, ideal_sensor=summ(ep), realistic_camera=summ(et), by_material=by_group)


# ------------------------------------------------------------------------------------------------- B. contrasts
PAIRS = [  # (case, label, target, host, horizontal distance m, frames, space for the eye[, eye height m])
    ("B_hvac_system", "envelope void vs wall (inside)", "TS_DEFECT_Insulation_Void", "South_Wall", 2.0,
     ["WINTER 07:00", "SUMMER 15:00"], "Zone"),
    ("B_hvac_system", "duct-insulation gap vs wrapped duct", "Duct_Insulation_Gap", "Discharge_Duct_1", 1.2,
     ["SUMMER 15:00", "WINTER 07:30"], "Plenum"),
    ("B_hvac_system", "reheat valve vs insulated pipe", "HW_Control_Valve", "HW_Supply_Pipe", 1.0,
     ["WINTER 07:30", "WINTER 10:00"], "Plenum"),
    ("B_hvac_system", "supply diffuser vs ceiling", "Supply_Diffuser_1", "Ceiling", 1.5, ["SUMMER 15:00", "WINTER 07:30"], "Zone", 1.5),
    ("B_hvac_system", "luminaire vs ceiling", "Luminaire_1", "Ceiling", 1.5, ["SUMMER 15:00"], "Zone", 1.5),
    ("C_doe_medium_office", "envelope void vs wall (outside)", "TS_DEFECT_Insulation_Void__exterior_",
     "Perimeter_top_ZN_1_Wall_South__exterior_", 8.0, ["WINTER 07:00", "SUMMER 14:00"], "Outdoors"),
]


def contrasts(rng):
    out = []
    for case, label, tgt, host, dist, frames, space, *eye_h in PAIRS:
        w = scene(case)
        k, h = w.prim_index(tgt), w.prim_index(host)
        pose = w.view_of(k, dist, space=space, height=eye_h[0] if eye_h else None)
        if pose is None:
            print("no view of", tgt); continue
        for lab in frames:
            tc = w.time_index(lab)
            clean = w.observe(pose, tc, ideal_like("handheld_320"), realism=CLEAN)
            ideal = w.observe(pose, tc, ideal_like("handheld_320"))
            t = stats(clean, k, h, "true"); i0 = stats(clean, k, h); i1 = stats(ideal, k, h)
            reals = [stats(w.observe(pose, tc, ThermalCamera.preset("handheld_320").randomized(rng), rng=rng), k, h) for _ in range(UNITS)]
            reals = [r for r in reals if r]
            if not (t and i1 and reals):
                continue
            rc = np.array([r["contrast"] for r in reals])
            out.append(dict(case=case[0], pair=label, frame=lab, distance_m=dist, pixels=t["n"],
                            true_K=round(t["contrast"], 2), clean_reading_K=round(i0["contrast"], 2),
                            ideal_sensor_K=round(i1["contrast"], 2), realistic_mean_K=round(float(rc.mean()), 2),
                            realistic_sd_K=round(float(rc.std()), 2),
                            sign_flip=bool(np.sign(rc.mean()) != np.sign(t["contrast"]) and abs(t["contrast"]) > 0.2),
                            realistic_ccr_median=round(float(np.median([r["ccr"] for r in reals])), 1),
                            realistic_p_detect=round(float(np.mean([r["detect"] for r in reals])), 2)))
            print(out[-1])
    return out


# ------------------------------------------------------------------------------------------------- C. distance
TARGETS = [  # (case, label, target, host, frame, space, distances)
    ("B_hvac_system", "envelope void, inside (B)", "TS_DEFECT_Insulation_Void", "South_Wall", "WINTER 07:00", "Zone",
     [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]),
    ("B_hvac_system", "duct-insulation gap (B)", "Duct_Insulation_Gap", "Discharge_Duct_1", "SUMMER 15:00", "Plenum",
     [0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]),
    ("C_doe_medium_office", "envelope void, outside (C)", "TS_DEFECT_Insulation_Void__exterior_",
     "Perimeter_top_ZN_1_Wall_South__exterior_", "WINTER 07:00", "Outdoors", [2.0, 4.0, 8.0, 12.0, 16.0, 24.0, 32.0]),
]


def detectability(rng):
    out = []
    for case, label, tgt, host, lab, space, dists in TARGETS:
        w = scene(case)
        k, h = w.prim_index(tgt), w.prim_index(host)
        tc = w.time_index(lab)
        for d in dists:
            pose = w.view_of(k, d, space=space)
            if pose is None:
                continue
            row = dict(target=label, frame=lab, distance_m=d)
            c0 = stats(w.observe(pose, tc, ideal_like("handheld_320"), realism=CLEAN), k, h)
            row["clean_ccr"] = None if c0 is None else (round(c0["ccr"], 1) if c0["sigma"] > 1e-6 else "inf")
            row["true_contrast_K"] = None if c0 is None else round(stats(w.observe(pose, tc, ideal_like("handheld_320"), realism=CLEAN), k, h, "true")["contrast"], 2)
            for pr in PRESETS:
                res = [stats(w.observe(pose, tc, ThermalCamera.preset(pr).randomized(rng), rng=rng), k, h) for _ in range(UNITS)]
                ok = [r for r in res if r]
                row[pr] = dict(pixels=int(np.median([r["n"] for r in ok])) if ok else 0,
                               contrast_K=round(float(np.median([r["contrast"] for r in ok])), 2) if ok else 0.0,
                               sigma_K=round(float(np.median([r["sigma"] for r in ok])), 3) if ok else 0.0,
                               ccr_median=round(float(np.median([r["ccr"] for r in ok])), 1) if ok else 0.0,
                               p_detect=round(float(np.mean([r["detect"] if r else False for r in res])), 2))
            out.append(row)
            print(row)
    return out


def main(parts="ABC"):
    """Run the experiments named in `parts` (default all); others are kept from the existing JSON."""
    t0 = time.time()
    rng = np.random.default_rng(2026)
    js = ROOT / "case_studies" / "measurement_gap.json"
    res = json.loads(js.read_text(encoding="utf-8")) if js.exists() and parts != "ABC" else {}
    res.update(k_ccr=K_CCR, n_min=N_MIN, units_per_view=UNITS, presets=PRESETS)
    if "A" in parts:
        res["A_pixel_gap"] = pixel_gap(rng)
    if "B" in parts:
        res["B_contrasts"] = contrasts(rng)
    if "C" in parts:
        res["C_detectability"] = detectability(rng)
    res["seconds"] = round(time.time() - t0)
    (ROOT / "case_studies" / "measurement_gap.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    A = res["A_pixel_gap"]
    L = ["# Measurement gap: camera reading vs true temperature", "",
         "Generated by `case_studies/measurement_gap.py` (cases B and C, embodied renderer, realism defaults).", "",
         "## A. Pixel gap over all scene cameras and preview frames", "",
         "| Condition | Pixels | Median abs error (K) | 95th pct (K) | > 1 K (%) | > 2 K (%) |", "|---|---|---|---|---|---|"]
    for key, nm in (("ideal_sensor", "ideal sensor, realistic scene"), ("realistic_camera", "320 x 240 camera units")):
        s = A[key]
        L.append(f"| {nm} | {s['pixels']} | {s['median_abs_K']} | {s['p95_abs_K']} | {s['share_over_1K']} | {s['share_over_2K']} |")
    L += ["", "## A2. Pixel gap by material group", "",
          "| Material group | Ideal sensor > 1 K (%) | Camera > 1 K (%) | Camera median abs (K) |", "|---|---|---|---|"]
    for g, v in A["by_material"].items():
        if v["ideal_sensor"]:
            L.append(f"| {g} | {v['ideal_sensor']['share_over_1K']} | {v['realistic_camera']['share_over_1K']} | {v['realistic_camera']['median_abs_K']} |")
    L += ["", "## B. Contrast fidelity (handheld_320 class, " + str(UNITS) + " units)", "",
          "| Case | Pair | Frame | Pixels | True (K) | Clean reading (K) | Ideal sensor (K) | Cameras, mean ± sd (K) | Sign flip | CCR | P(detect) |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["B_contrasts"]:
        L.append(f"| {r['case']} | {r['pair']} | {r['frame']} | {r['pixels']} | {r['true_K']} | {r['clean_reading_K']} | "
                 f"{r['ideal_sensor_K']} | {r['realistic_mean_K']} ± {r['realistic_sd_K']} | {'yes' if r['sign_flip'] else ''} | "
                 f"{r['realistic_ccr_median']} | {r['realistic_p_detect']} |")
    L += ["", f"## C. Detectability vs distance: P(N >= {N_MIN} px and CCR >= {K_CCR}) over {UNITS} random units "
          "(median pixels, CCR)", "",
          "| Target | Distance (m) | True contrast (K) | Clean image CCR | " + " | ".join(PRESETS) + " |", "|---|---|---|---|" + "---|" * len(PRESETS)]
    for r in res["C_detectability"]:
        L.append(f"| {r['target']} | {r['distance_m']} | {r['true_contrast_K']} | {r['clean_ccr']} | " +
                 " | ".join(f"{r[p]['p_detect']:.2f} ({r[p]['pixels']} px, {r[p]['ccr_median']})" for p in PRESETS) + " |")
    (ROOT / "docs" / "measurement_gap.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote docs/measurement_gap.md in", res["seconds"], "s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ABC")      # e.g. "A" re-runs the pixel gap only
