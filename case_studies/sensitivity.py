"""One-at-a-time sensitivity of thermal contrasts to the non-simulated parameters (case B).

Each parameter is set to a low and a high plausible value while all others stay at their defaults; the scene is
rebuilt (the EnergyPlus run is cached) and five apparent-temperature contrasts are recomputed.
Writes case_studies/sensitivity.json and docs/sensitivity.md.  Run:  python case_studies/sensitivity.py
"""
import json
import logging
import sys
from pathlib import Path

from pxr import Usd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402
from thermoscene.render.shading import Series, labels  # noqa: E402
from thermoscene.thermal import radiometry as rad  # noqa: E402

CASE = ROOT / "case_studies" / "B_hvac_system" / "case.yaml"
PARAMS = [  # (label, override path, low, high, default)
    ("Insulation conductivity (W/m·K)", ["thermal", "insulation", "conductivity"], 0.030, 0.050, 0.040),
    ("Plenum film coefficient (W/m²K)", ["thermal", "film_coefficients", "plenum"], 6.0, 10.0, 8.0),
    ("Stagnant-water time constant (steps)", ["thermal", "stagnation_tau_steps", "water"], 3, 12, 6),
    ("Diffuser face effectiveness η_max", ["thermal", "diffuser", "eta_max"], 0.70, 0.95, 0.85),
    ("Duct-wrap facing emissivity", ["materials", "fsk_foil", "emissivity"], 0.10, 0.60, 0.30),
    ("Motor temperature rise (K)", ["thermal", "motor_rise"], 10.0, 20.0, 15.0),
    ("Camera emissivity setting", ["camera", "emissivity_setting"], 0.90, 1.00, 0.95),
]
METRICS = [  # (label, hot path, reference path, frame)
    ("Wall defect − wall, winter 07:00", "/World/Office/Surfaces/TS_DEFECT_Insulation_Void", "/World/Office/Surfaces/South_Wall", "WINTER 01-15 07:00"),
    ("Duct insulation gap − wrapped duct, summer 15:00", "/World/Plenum/Elements/Duct_Insulation_Gap", "/World/Plenum/Elements/Discharge_Duct_1", "SUMMER 07-16 15:00"),
    ("Reheat valve − pipe jacket, winter 10:00", "/World/Plenum/Elements/HW_Control_Valve", "/World/Plenum/Elements/HW_Supply_Pipe", "WINTER 01-15 10:00"),
    ("Diffuser − ceiling, summer 15:00", "/World/Office/Elements/Supply_Diffuser_1", "/World/Office/Surfaces/Ceiling", "SUMMER 07-16 15:00"),
    ("Pump motor − roof air, summer 09:00", "Motor", None, "SUMMER 07-16 09:00"),
]


def nest(path, value):
    d = value
    for k in reversed(path):
        d = {k: d}
    return d


def apparent(stage, ser, prim, tc, cam_eps):
    env = stage.GetPrimAtPath(str(prim.GetPath()).split("/Surfaces/")[0].split("/Elements/")[0])
    t = ser.get(prim, "thermal:surfaceTemperature", tc)
    eps = prim.GetAttribute("thermal:emissivity").Get()
    l_obs = rad.observed_radiance(t, eps, ser.get(env, "thermal:reflectedTemperature", tc))
    return float(rad.apparent_temperature(l_obs, cam_eps, 20.0)), env


def metrics(usd, cam_eps):
    st = Usd.Stage.Open(str(usd)); ser = Series(st); labs = labels(st)
    out = {}
    for name, a, b, frame in METRICS:
        tc = labs.index(frame)
        if a == "Motor":
            pa = next(p for p in Usd.PrimRange(st.GetPrimAtPath("/World")) if p.GetAttribute("semantic:class").Get() == "Motor")
            ta, env = apparent(st, ser, pa, tc, cam_eps)
            out[name] = ta - ser.get(env, "thermal:airTemperature", tc)
        else:
            ta, _ = apparent(st, ser, st.GetPrimAtPath(a), tc, cam_eps)
            tb, _ = apparent(st, ser, st.GetPrimAtPath(b), tc, cam_eps)
            out[name] = ta - tb
    return out


def main():
    logging.basicConfig(level=logging.WARNING)
    root = ROOT / "case_studies" / "B_hvac_system" / "sensitivity"
    base_usd = pipeline.build(CASE, overrides={"output_dir": str(root / "base")})
    base = metrics(base_usd, 0.95)
    rows = []
    for label, path, lo, hi, default in PARAMS:
        res = {}
        for tag, val in (("low", lo), ("high", hi)):
            if path[0] == "camera":
                res[tag] = metrics(base_usd, val)
            else:
                usd = pipeline.build(CASE, overrides={**nest(path, val), "output_dir": str(root / f"{path[-1]}_{tag}")})
                res[tag] = metrics(usd, 0.95)
        rows.append(dict(parameter=label, low=lo, high=hi, default=default, results=res))
        print(label, {m: (round(res["low"][m], 2), round(res["high"][m], 2)) for m in base})
    (ROOT / "case_studies" / "sensitivity.json").write_text(json.dumps(dict(base=base, rows=rows), indent=2))
    lines = ["| Parameter (low – high) | " + " | ".join(m for m, *_ in METRICS) + " |", "|---|" + "---|" * len(METRICS)]
    lines.append("| **Default** | " + " | ".join(f"**{base[m]:+.1f}**" for m, *_ in METRICS) + " |")
    for r in rows:
        cells = []
        for m, *_ in METRICS:
            lo, hi = r["results"]["low"][m], r["results"]["high"][m]
            cells.append("—" if abs(lo - base[m]) < 0.05 and abs(hi - base[m]) < 0.05 else f"{lo:+.1f} … {hi:+.1f}")
        lines.append(f"| {r['parameter']} ({r['low']} – {r['high']}) | " + " | ".join(cells) + " |")
    (ROOT / "docs" / "sensitivity.md").write_text(
        "# Sensitivity of apparent thermal contrasts (case B)\n\nApparent temperature contrast (K) seen by a camera with "
        "emissivity setting 0.95 and reflected-temperature setting 20 °C; one parameter varied at a time. "
        "— = change below 0.05 K.\n\n" + "\n".join(lines) + "\n")
    print("base", base)


if __name__ == "__main__":
    main()
