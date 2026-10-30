"""Visibility and energy consequence of the injected faults over a full year (case B, Phoenix; case C, Chicago).

The case is rebuilt for the whole weather year (EnergyPlus run cached in .sim_cache; time series in the NPZ sidecar;
no cameras). For every 15-minute step it computes what a camera with default settings (emissivity 0.95, reflected
temperature 20 °C) would read across each fault, and what the fault does to the heat flows:
  - envelope void: apparent contrast to the insulated wall; EnergyPlus conduction through the void against the same
    area of insulated wall (inside face; annual heat lost and gained);
  - duct insulation gap: apparent contrast to the wrapped duct while air flows; extra heat picked up by the supply air
    (the pipeline's bare_duct and insulated_flat relations) against the office's sensible cooling.
Writes case_studies/annual.json and docs/annual.md.  Run:  python case_studies/annual.py
"""
import json
import logging
import sys
from pathlib import Path

import numpy as np
import yaml
from pxr import Usd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402
from thermoscene.energyplus.results import Results  # noqa: E402
from thermoscene.render.shading import Series, labels  # noqa: E402
from thermoscene.thermal import radiometry as rad  # noqa: E402

O, P = "/World/Office", "/World/Plenum"
THRESHOLDS = (0.1, 0.5, 1.0)      # K: 2 x NETD, and contrasts a person or a detector would notice


def series(stage, ser, path, attr, n):
    p = stage.GetPrimAtPath(path)
    return np.array([ser.get(p, attr, t) for t in range(n)])


def apparent(t, eps, t_refl, cam=(0.95, 20.0)):
    return rad.apparent_temperature(rad.observed_radiance(t, eps, t_refl), *cam)


def share(x, mask):
    return {f">= {k} K": round(100 * float(np.mean(np.abs(x[mask]) >= k)), 1) for k in THRESHOLDS}


def year_scene(case_dir):
    """Build a case for the whole weather year (sidecar storage, no cameras); return stage, series, labels, results."""
    out = case_dir / "annual"
    usd = pipeline.build(case_dir / "case.yaml", overrides={
        "energyplus": {"run_periods": [{"name": "YEAR", "start": "01-01", "end": "12-31"}]},
        "storage": {"time_series": "sidecar"}, "cameras": [], "output_dir": str(out)})
    st = Usd.Stage.Open(str(usd)); ser = Series(st); labs = labels(st)
    r = None                                                     # the cached annual EnergyPlus run (same time axis)
    for cand in sorted((case_dir / ".sim_cache").glob("*/eplusout.sql"), key=lambda q: q.stat().st_mtime, reverse=True):
        rr = Results(cand)
        if rr.n == len(labs):
            r = rr
            break
    return st, ser, labs, r, out


def envelope_void(st, ser, labs, r, zone, wall_name, void_name):
    n = len(labs)
    hours = np.array([int(lab.split()[-1][:2]) + int(lab.split()[-1][3:]) / 60 for lab in labs])
    day = np.array([lab.split()[1] for lab in labs])
    occupied = (hours > 8) & (hours <= 18)
    dt = int(st.GetRootLayer().customLayerData["thermal:timestepMinutes"]) / 60
    wall, void = f"{zone}/Surfaces/{wall_name}", f"{zone}/Surfaces/{void_name}"
    eps_w = st.GetPrimAtPath(wall).GetAttribute("thermal:emissivity").Get()
    eps_v = st.GetPrimAtPath(void).GetAttribute("thermal:emissivity").Get()
    t_refl = series(st, ser, zone, "thermal:reflectedTemperature", n)
    c_env = apparent(series(st, ser, void, "thermal:surfaceTemperature", n), eps_v, t_refl) - \
        apparent(series(st, ser, wall, "thermal:surfaceTemperature", n), eps_w, t_refl)
    sign_days = sum(1 for d in np.unique(day) if np.any(c_env[day == d] >= 0.5) and np.any(c_env[day == d] <= -0.5))
    var, var_a = "Surface Inside Face Conduction Heat Transfer Rate", "Surface Inside Face Conduction Heat Transfer Rate per Area"
    qv, qw = r.series(var_a, void_name.upper()), r.series(var_a, wall_name.upper())
    a_v = float(np.median([p / a for p, a in zip(r.series(var, void_name.upper()), qv) if abs(a) > 0.5]))
    a_w = float(np.median([p / a for p, a in zip(r.series(var, wall_name.upper()), qw) if abs(a) > 0.5]))
    extra = (qv - qw) * a_v * dt / 1000                          # kWh per step through the void beyond the insulated wall
    without = qw * (a_w + a_v) * dt / 1000                       # kWh per step through the whole wall, no void
    with_void = (qw * a_w + qv * a_v) * dt / 1000
    return {
        "apparent contrast, share of all steps (%)": share(c_env, np.ones(n, bool)),
        "apparent contrast, share of occupied steps 08-18 h (%)": share(c_env, occupied),
        "apparent contrast, 5th / 50th / 95th percentile (K)": [round(float(np.percentile(c_env, q)), 2) for q in (5, 50, 95)],
        "days with both a >= +0.5 K and a <= -0.5 K contrast": f"{sign_days} of {len(np.unique(day))}",
        "void area (m2) / insulated wall area (m2)": [round(a_v, 2), round(a_w, 2)],
        "extra heat loss through the void, annual (kWh)": round(float(-extra[extra < 0].sum()), 1),
        "extra heat gain through the void, annual (kWh)": round(float(extra[extra > 0].sum()), 1),
        "void vs insulated wall, annual conduction magnitude (ratio)": round(float(np.abs(qv).sum() / np.abs(qw).sum()), 2),
        "whole wall without / with void, annual heat gain (kWh)": [round(float(without[without > 0].sum()), 1), round(float(with_void[with_void > 0].sum()), 1)],
        "whole wall without / with void, annual heat loss (kWh)": [round(float(-without[without < 0].sum()), 1), round(float(-with_void[with_void < 0].sum()), 1)],
    }


def main():
    logging.basicConfig(level=logging.WARNING)
    cs = ROOT / "case_studies"
    out = {}
    stc, serc, labc, rc, _ = year_scene(cs / "C_doe_medium_office")
    out["C envelope void (Chicago)"] = envelope_void(stc, serc, labc, rc, "/World/Perimeter_top_ZN_1",
                                                     "Perimeter_top_ZN_1_Wall_South", "TS_DEFECT_Insulation_Void")
    st, ser, labs, r, OUT = year_scene(cs / "B_hvac_system")
    n = len(labs)
    dt = int(st.GetRootLayer().customLayerData["thermal:timestepMinutes"]) / 60
    out = {"steps": n, "step_hours": dt, "B envelope void (Phoenix)": envelope_void(
        st, ser, labs, r, O, "South_Wall", "TS_DEFECT_Insulation_Void"), **out}

    # ---- duct insulation gap
    th = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))["thermal"]
    h_i, h_o, k = th["film_coefficients"]["inside_air"], th["film_coefficients"]["plenum"], th["insulation"]["conductivity"]
    delta = th["insulation"]["duct_wrap_m"]
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    size = {e["id"]: e["size"] for e in s["elements"]}
    area = np.pi * float(size["Plenum/Discharge_Duct_1"].split()[0]) / 1000 * float(size["Plenum/Duct_Insulation_Gap"].split()[0])
    gap, duct = P + "/Elements/Duct_Insulation_Gap", P + "/Elements/Discharge_Duct_1"
    m = series(st, ser, duct, "mep:fluidMassFlow", n); tf = series(st, ser, duct, "mep:fluidTemperature", n)
    ta = series(st, ser, P, "thermal:airTemperature", n); tz = series(st, ser, O, "thermal:airTemperature", n)
    t_refl_p = series(st, ser, P, "thermal:reflectedTemperature", n)
    eps_g = st.GetPrimAtPath(gap).GetAttribute("thermal:emissivity").Get()
    eps_d = st.GetPrimAtPath(duct).GetAttribute("thermal:emissivity").Get()
    c_gap = apparent(series(st, ser, gap, "thermal:surfaceTemperature", n), eps_g, t_refl_p) - \
        apparent(series(st, ser, duct, "thermal:surfaceTemperature", n), eps_d, t_refl_p)
    flow = m > 0
    gain = np.where(flow, area * (ta - tf) * (1 / (1 / h_i + 1 / h_o) - 1 / (1 / h_i + delta / k + 1 / h_o)), 0.0)
    cool = np.where(flow, m * 1005.0 * (tz - tf), 0.0)
    out["B duct insulation gap (Phoenix)"] = {
        "apparent contrast while air flows, share of flowing steps (%)": share(c_gap, flow),
        "apparent contrast while air flows, 5th / 50th / 95th percentile (K)": [round(float(np.percentile(c_gap[flow], q)), 2) for q in (5, 50, 95)],
        "unwrapped area (m2)": round(area, 3),
        "extra heat gain to the supply air, annual net (kWh)": round(float(gain.sum() * dt / 1000), 1),
        "office sensible cooling delivered, annual (kWh)": round(float(cool[cool > 0].sum() * dt / 1000), 1),
        "extra gain as share of the office's sensible cooling (%)": round(100 * float(gain[cool > 0].sum() / cool[cool > 0].sum()), 2),
    }
    (ROOT / "case_studies" / "annual.json").write_text(encoding="utf-8", data=json.dumps(out, indent=2, ensure_ascii=False))
    lines = ["# Faults over a full year (case B, Phoenix TMY3; case C, Chicago TMY3)", "",
             f"{n} time steps of {dt * 60:.0f} min. Apparent contrasts as read by a camera set to emissivity 0.95 and "
             "reflected temperature 20 °C; true temperatures from the scene. Energy from EnergyPlus conduction (void) and the "
             "pipeline's duct relations (gap), per zone instance. Generated by `case_studies/annual.py`.", ""]
    for grp, vals in out.items():
        if not isinstance(vals, dict):
            continue
        lines += [f"## {grp}", "", "| Quantity | Value |", "|---|---|"]
        lines += [f"| {key} | {', '.join(f'{a}: {b}' for a, b in v.items()) if isinstance(v, dict) else (' / '.join(map(str, v)) if isinstance(v, list) else v)} |"
                  for key, v in vals.items()]
        lines.append("")
    (ROOT / "docs" / "annual.md").write_text(encoding="utf-8", data="\n".join(lines))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
