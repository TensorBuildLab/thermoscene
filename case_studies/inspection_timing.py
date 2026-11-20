"""When should an inspection agent look? Detectability of the injected faults over a simulated year, and how much an
energy model's prediction of the operating state improves the choice of inspection time.

For each fault (envelope void of case B, Phoenix, and of case C, Chicago, seen from inside; duct-insulation gap of
case B) the apparent contrast of every 15-minute step of the TMY3 year is computed from the reference viewpoint in the
realistic scene (directional emissivity, specular reflections, within-surface variation; ThermalWorld.contrast_series),
as an ideal sensor set to emissivity 0.95 and 20 degC reads it. A camera detects the fault at a step when the contrast
reaches K_CCR times the spatial variation of the surroundings, sigma, measured by rendering that viewpoint with random
units of a 320 x 240 camera class (realistic sensor and scene). Inspection-time policies are compared by the share of their steps at which the fault is detectable:

* any time;
* working hours (08:00-18:00);
* the envelope-thermography rule of thumb: indoor-outdoor air temperature difference of at least 10 K
  (and a stricter variant with no sun on the site during the preceding 12 h);
* model-informed, without knowledge of the fault: steps at which the energy model predicts a strong heat flow through
  the healthy construction (inside-face conduction flux of the insulated wall in its top 20 % of the year), or, for a
  duct, supply air flowing with a supply-plenum temperature difference in the top 20 % of flowing steps.

Writes case_studies/inspection_timing.json and docs/inspection_timing.md.  Run:  python case_studies/inspection_timing.py
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "case_studies"))
from annual import apparent, series, year_scene  # noqa: E402
from measurement_gap import CLEAN, K_CCR, N_MIN, ideal_like, stats  # noqa: E402
from thermoscene.embodied import ThermalCamera, ThermalWorld  # noqa: E402

UNITS = 32                     # random camera units per frame for sigma (2 frames -> 64 samples per reference view)
SIGMA_SEED = 7                 # each reference view draws its units from its own generator (SIGMA_SEED + view index)
TOP = 0.8                     # model-informed policy: steps above this quantile of the predicted driving quantity
K_SENS = (2.0, 3.0, 5.0)       # detection thresholds reported for sensitivity


def clutter_sigma(case, target, host, dist, space, frames, rng, samples=None):
    """Median spatial standard deviation of the reading around `target` (and its pixel count) at the reference view;
    `samples`, if given, receives the value of every camera unit and frame."""
    w = ThermalWorld(ROOT / "case_studies" / case / "output" / f"{case}.usda")
    k, h = w.prim_index(target), w.prim_index(host)
    pose = w.view_of(k, dist, space=space)
    sig, n, clean = [], [], []
    for lab in frames:
        tc = w.time_index(lab)
        for _ in range(UNITS):
            s = stats(w.observe(pose, tc, ThermalCamera.preset("handheld_320").randomized(rng), rng=rng), k, h)
            if s:
                sig.append(s["sigma"]); n.append(s["n"])
        c = stats(w.observe(pose, tc, ideal_like("handheld_320"), realism=CLEAN), k, h)
        clean.append(c["sigma"] if c else np.nan)
    if samples is not None:
        samples.extend(sig)
    return float(np.median(sig)), int(np.median(n)), float(np.nanmedian(clean))


def hours_of(labs):
    hm = [lab.split()[-1] for lab in labs]
    return np.array([int(x[:2]) + int(x[3:]) / 60 for x in hm]), np.array([int(lab.split()[-2][:2]) for lab in labs])


def evaluate(c_app, policies, sigma, sigma_range=None):
    """Detection share per policy at K_CCR x sigma; with `sigma_range` (sigma of the noisiest and quietest camera units
    considered), also the share for those units, noisiest first."""
    det = np.abs(c_app) >= K_CCR * sigma
    out = {}
    for name, mask in policies.items():
        m = mask & np.isfinite(c_app)
        out[name] = dict(share_of_year=round(100 * float(m.mean()), 1),
                         p_detect=round(100 * float(det[m].mean()), 1) if m.any() else None,
                         cold_share=round(100 * float((c_app[m & det] < 0).mean()), 1) if (m & det).any() else None,
                         p_detect_by_threshold={str(kk): round(100 * float((np.abs(c_app[m]) >= kk * sigma).mean()), 1)
                                                for kk in K_SENS} if m.any() else None)
        if sigma_range and m.any():
            out[name]["p_detect_unit_range"] = [round(100 * float((np.abs(c_app[m]) >= K_CCR * s).mean()), 1)
                                                for s in sorted(sigma_range, reverse=True)]
    return out, det


def hour_month(det, hours, months, mask=None):
    m = np.ones_like(det) if mask is None else mask
    grid = np.full((12, 24), np.nan)
    for mo in range(1, 13):
        for hr in range(24):
            sel = (months == mo) & (np.floor(hours - 1e-9) % 24 == hr) & m
            if sel.any():
                grid[mo - 1, hr] = det[sel].mean()
    return grid


def realistic_contrast(out_dir, target, host, dist, space):
    """Contrast of `target` against its host at every step of the year scene, from the reference viewpoint."""
    w = ThermalWorld(next(Path(out_dir).glob("*.usda")))
    k, h = w.prim_index(target), w.prim_index(host)
    pose = w.view_of(k, dist, space=space)
    tx = float(np.tan(np.radians(45.0) / 2))
    c, info = w.contrast_series(pose, k, h, tx, tx * 0.75, 320, 240, space=space)
    return c


def envelope(case_dir, zone, wall, void, sigma, dist=2.0, sigma_range=None):
    st, ser, labs, r, out_dir = year_scene(case_dir)
    n = len(labs)
    hours, months = hours_of(labs)
    wpath, vpath = f"{zone}/Surfaces/{wall}", f"{zone}/Surfaces/{void}"
    t_refl = series(st, ser, zone, "thermal:reflectedTemperature", n)
    eps_w = st.GetPrimAtPath(wpath).GetAttribute("thermal:emissivity").Get()
    eps_v = st.GetPrimAtPath(vpath).GetAttribute("thermal:emissivity").Get()
    c_clean = apparent(series(st, ser, vpath, "thermal:surfaceTemperature", n), eps_v, t_refl) - \
        apparent(series(st, ser, wpath, "thermal:surfaceTemperature", n), eps_w, t_refl)
    c = realistic_contrast(out_dir, void, wall, dist, "Zone")
    t_in = series(st, ser, zone, "thermal:airTemperature", n)
    t_out = series(st, ser, "/World/Outdoors", "thermal:airTemperature", n)
    sun = series(st, ser, "/World/Outdoors", "thermal:horizontalSolar", n)
    steps12 = int(round(12 * 60 / float(st.GetRootLayer().customLayerData["thermal:timestepMinutes"])))
    no_sun = np.array([sun[max(0, i - steps12):i + 1].max() < 1.0 for i in range(n)])
    q = np.abs(r.series("Surface Inside Face Conduction Heat Transfer Rate per Area", wall.upper()))
    work = (hours > 8) & (hours <= 18)
    pol = {"any time": np.ones(n, bool), "working hours 08-18": work,
           "rule of thumb: |T_in - T_out| >= 10 K": np.abs(t_in - t_out) >= 10,
           "rule of thumb + no sun in the previous 12 h": (np.abs(t_in - t_out) >= 10) & no_sun,
           "model-informed: healthy-wall heat flux in its top 20 %": q >= np.quantile(q, TOP),
           "model-informed, working hours": (q >= np.quantile(q[work], TOP)) & work}
    res, det = evaluate(c, pol, sigma, sigma_range)
    det_ = np.abs(c) >= K_CCR * sigma
    day = np.array([lab.split()[-2] for lab in labs])
    flips = sum(1 for dd in np.unique(day) if np.any(det_[day == dd] & (c[day == dd] > 0)) and
                np.any(det_[day == dd] & (c[day == dd] < 0)))
    return res, hour_month(det, hours, months).round(3).tolist(), dict(
        contrast_p5_p50_p95=np.percentile(c, [5, 50, 95]).round(2).tolist(),
        clean_contrast_p5_p50_p95=np.percentile(c_clean, [5, 50, 95]).round(2).tolist(),
        cold_share_working_hours=round(100 * float((c[work & det_] < 0).mean()), 1),
        cold_share_outside_working_hours=round(100 * float((c[~work & det_] < 0).mean()), 1),
        days_with_detectable_warm_and_cold=f"{flips} of {len(np.unique(day))}")


def duct_gap(case_dir, sigma, dist=1.2, sigma_range=None):
    st, ser, labs, r, out_dir = year_scene(case_dir)
    n = len(labs)
    hours, months = hours_of(labs)
    P = "/World/Plenum"
    gap, duct = P + "/Elements/Duct_Insulation_Gap", P + "/Elements/Discharge_Duct_1"
    t_refl = series(st, ser, P, "thermal:reflectedTemperature", n)
    eg = st.GetPrimAtPath(gap).GetAttribute("thermal:emissivity").Get()
    ed = st.GetPrimAtPath(duct).GetAttribute("thermal:emissivity").Get()
    c_clean = apparent(series(st, ser, gap, "thermal:surfaceTemperature", n), eg, t_refl) - \
        apparent(series(st, ser, duct, "thermal:surfaceTemperature", n), ed, t_refl)
    c = realistic_contrast(out_dir, "Duct_Insulation_Gap", "Discharge_Duct_1", dist, "Plenum")
    flow = series(st, ser, duct, "mep:fluidMassFlow", n) > 0
    dT = np.abs(series(st, ser, duct, "mep:fluidTemperature", n) - series(st, ser, P, "thermal:airTemperature", n))
    work = (hours > 8) & (hours <= 18)
    pol = {"any time": np.ones(n, bool), "working hours 08-18": work, "while supply air flows": flow,
           "model-informed: flowing, supply-plenum difference in its top 20 %": flow & (dT >= np.quantile(dT[flow], TOP))}
    res, det = evaluate(c, pol, sigma, sigma_range)
    return res, hour_month(det, hours, months).round(3).tolist(), dict(
        contrast_p5_p50_p95=np.percentile(c[flow], [5, 50, 95]).round(2).tolist(),
        clean_contrast_p5_p50_p95=np.percentile(c_clean[flow], [5, 50, 95]).round(2).tolist())


def main():
    cs = ROOT / "case_studies"
    out = dict(k_ccr=K_CCR, n_min=N_MIN, camera="handheld_320 class, random units", top_quantile=TOP)
    refs = {"B envelope void (Phoenix), inside, 2 m": ("B_hvac_system", "TS_DEFECT_Insulation_Void", "South_Wall", 2.0, "Zone"),
            "C envelope void (Chicago), inside, 2 m": ("C_doe_medium_office", "TS_DEFECT_Insulation_Void",
                                                        "Perimeter_top_ZN_1_Wall_South", 2.0, "Zone"),
            "B duct-insulation gap (Phoenix), 1.2 m": ("B_hvac_system", "Duct_Insulation_Gap", "Discharge_Duct_1", 1.2, "Plenum")}
    sig = {}
    rng_of = {}
    for i, (name, (case, tgt, host, d, sp)) in enumerate(refs.items()):
        frames = ["WINTER 07:00", "SUMMER 15:00"] if case[0] == "B" else ["WINTER 07:00", "SUMMER 14:00"]
        draws = []
        s, npx, s_clean = clutter_sigma(case, tgt, host, d, sp, frames, np.random.default_rng(SIGMA_SEED + i), draws)
        sig[name] = s
        rng_of[name] = tuple(float(v) for v in np.percentile(draws, [10, 90]))
        out.setdefault("reference_view", {})[name] = dict(sigma_K=round(s, 3), sigma_units_p10_p90_K=[round(v, 3) for v in rng_of[name]], samples=len(draws),
                                                         fault_pixels=npx, clean_sigma_K=round(s_clean, 4),
                                                         min_detectable_contrast_K=round(K_CCR * s, 2))
        print(name, out["reference_view"][name])
    k = "B envelope void (Phoenix), inside, 2 m"
    out[k], grid_b, extra_b = envelope(cs / "B_hvac_system", "/World/Office", "South_Wall", "TS_DEFECT_Insulation_Void", sig[k],
                                       sigma_range=rng_of[k])
    out[k]["contrast"] = extra_b
    k2 = "C envelope void (Chicago), inside, 2 m"
    out[k2], grid_c, extra_c = envelope(cs / "C_doe_medium_office", "/World/Perimeter_top_ZN_1", "Perimeter_top_ZN_1_Wall_South",
                                        "TS_DEFECT_Insulation_Void", sig[k2], sigma_range=rng_of[k2])
    out[k2]["contrast"] = extra_c
    k3 = "B duct-insulation gap (Phoenix), 1.2 m"
    out[k3], grid_g, extra_g = duct_gap(cs / "B_hvac_system", sig[k3], sigma_range=rng_of[k3])
    out[k3]["contrast"] = extra_g
    out["hour_month_detect"] = {k: grid_b, k2: grid_c, k3: grid_g}
    (cs / "inspection_timing.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    L = ["# When to inspect: detectability of the faults over a simulated year", "",
         f"Detectable: |apparent contrast| >= {K_CCR} x sigma (spatial variation of the reading around the fault at the "
         "reference view, 320 x 240 camera class, random units). Generated by `case_studies/inspection_timing.py`.", ""]
    for name in refs:
        rv = out["reference_view"][name]
        L += [f"## {name}", "", f"sigma = {rv['sigma_K']} K (median of {rv['samples']} camera units x frames; "
              f"10th-90th percentile of units {rv['sigma_units_p10_p90_K'][0]}-{rv['sigma_units_p10_p90_K'][1]} K; "
              f"clean image: {rv['clean_sigma_K']} K), {rv['fault_pixels']} fault pixels, "
              f"minimum detectable contrast {rv['min_detectable_contrast_K']} K; contrast 5th/50th/95th percentile "
              f"{out[name]['contrast']['contrast_p5_p50_p95']} K.", "",
              "| Inspection-time policy | Share of the year (%) | Detectable (%) | Detectable, noisiest-quietest units (%) | "
              "Cold among detections (%) |", "|---|---|---|---|---|"]
        for pol, v in out[name].items():
            if pol == "contrast":
                continue
            rr = v.get('p_detect_unit_range') or ['', '']
            L.append(f"| {pol} | {v['share_of_year']} | {v['p_detect']} | {rr[0]}-{rr[1]} | {v['cold_share']} |")
        L.append("")
    (ROOT / "docs" / "inspection_timing.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
