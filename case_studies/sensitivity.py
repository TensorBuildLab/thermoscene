"""One-at-a-time sensitivity of apparent thermal contrasts to the non-simulated parameters (case B).

Each parameter is set to a low and a high value while all others stay at their defaults; the scene is rebuilt (the
EnergyPlus run is cached, or re-run when the parameter enters the model, like the defect's cavity resistance) and
eight apparent-temperature contrasts are recomputed as a camera would read them.
Writes case_studies/sensitivity.json and docs/sensitivity.md.  Run:  python case_studies/sensitivity.py

The ranges are illustrative bounds around the defaults (not a calibrated uncertainty analysis). One-at-a-time
variation shows which contrasts a parameter moves; because each element model has its own parameters, a parameter
can only move the contrasts of the elements it governs.
"""
import json
import logging
import sys
from pathlib import Path

from pxr import Usd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402
from thermoscene.render.shading import Series, jet_delta, labels  # noqa: E402
from thermoscene.thermal import radiometry as rad  # noqa: E402

CASE = ROOT / "case_studies" / "B_hvac_system" / "case.yaml"
PARAMS = [  # (label, override path, low, high, default)
    ("Insulation conductivity (W/m·K)", ["thermal", "insulation", "conductivity"], 0.030, 0.050, 0.040),
    ("Plenum film coefficient (W/m²K)", ["thermal", "film_coefficients", "plenum"], 6.0, 10.0, 8.0),
    ("Stagnant-water time constant, insulated pipe (min)", ["thermal", "stagnation_tau_min", "water"], 60, 240, 120),
    ("Stagnant-water time constant, bare valve (min)", ["thermal", "stagnation_tau_min", "valve"], 15, 60, 30),
    ("Envelope cavity resistance (m²K/W)", ["fault_defaults", "envelope_defect", "cavity_resistance"], 0.10, 0.30, 0.17),
    ("Diffuser face effectiveness η_max", ["thermal", "diffuser", "eta_max"], 0.70, 0.95, 0.85),
    ("Ceiling-jet amplitude a_max", ["thermal", "diffuser", "jet_strength_max"], 0.30, 0.70, 0.50),
    ("Ceiling-jet decay length L_0 (m)", ["thermal", "diffuser", "jet_len0"], 0.10, 0.30, 0.15),
    ("Duct-wrap facing emissivity", ["materials", "fsk_foil", "emissivity"], 0.10, 0.60, 0.30),
    ("Motor temperature rise (K)", ["thermal", "motor_rise"], 10.0, 20.0, 15.0),
    ("Condenser-coil fraction φ_c", ["thermal", "condenser_coil_fraction"], 0.40, 0.80, 0.60),
    ("AHU casing coupling", ["thermal", "casing_coupling", "ahu"], 0.02, 0.10, 0.05),
    ("Sol-air solar fraction f_s", ["thermal", "sol_air", "vertical_solar_fraction"], 0.40, 0.80, 0.60),
    ("Camera emissivity setting", ["camera", "emissivity_setting"], 0.90, 1.00, 0.95),
    ("Camera reflected-temperature setting (°C)", ["camera", "reflected_setting_c"], 10.0, 30.0, 20.0),
]
W, S = "WINTER 01-15 ", "SUMMER 07-16 "
O, P, X = "/World/Office", "/World/Plenum/Elements", "/World/Outdoors"
METRICS = [  # (short label, description, hot, reference, frame); "class:<Class>" picks the first prim of that class
    ("M1", "Wall defect − wall, winter 07:00", O + "/Surfaces/TS_DEFECT_Insulation_Void", O + "/Surfaces/South_Wall", W + "07:00"),
    ("M2", "Duct gap − wrapped duct, summer 15:00", P + "/Duct_Insulation_Gap", P + "/Discharge_Duct_1", S + "15:00"),
    ("M3", "Reheat valve − pipe jacket, winter 10:00", P + "/HW_Control_Valve", P + "/HW_Supply_Pipe", W + "10:00"),
    ("M4", "Diffuser face − ceiling, summer 15:00", O + "/Elements/Supply_Diffuser_1", O + "/Surfaces/Ceiling", S + "15:00"),
    ("M5", "Ceiling 0.3 m beyond the diffuser edge (jet) − ceiling, summer 15:00", "jet", O + "/Surfaces/Ceiling", S + "15:00"),
    ("M6", "Pump motor − roof deck, summer 09:00", "class:Motor", "class:RoofSurface", S + "09:00"),
    ("M7", "Condenser coil − roof deck, summer 15:00", "class:CondenserCoil", "class:RoofSurface", S + "15:00"),
    ("M8", "AHU fan section − roof deck, summer 15:00", X + "/Elements/AHU_Central_AHU_Supply_Fan", "class:RoofSurface", S + "15:00"),
]


def nest(path, value):
    d = value
    for k in reversed(path):
        d = {k: d}
    return d


def prim(st, spec):
    if spec.startswith("class:"):
        return next(p for p in Usd.PrimRange(st.GetPrimAtPath("/World")) if p.HasAttribute("thermal:surfaceTemperature")
                    and p.GetAttribute("semantic:class").Get() == spec[6:])
    return st.GetPrimAtPath(spec)


def apparent(st, ser, p, tc, cam, t=None):
    """Camera reading of prim p (true temperature t if given), with the camera settings cam = (eps, reflected)."""
    env = st.GetPrimAtPath(str(p.GetPath()).split("/Surfaces/")[0].split("/Elements/")[0])
    t = ser.get(p, "thermal:surfaceTemperature", tc) if t is None else t
    l_obs = rad.observed_radiance(t, p.GetAttribute("thermal:emissivity").Get(), ser.get(env, "thermal:reflectedTemperature", tc))
    return float(rad.apparent_temperature(l_obs, *cam))


def jet_point(st, ser, tc, offset=0.3):
    """True temperature of the ceiling `offset` m beyond the edge of the first diffuser, on its axis."""
    d = st.GetPrimAtPath(O + "/Elements/Supply_Diffuser_1")
    ceil = st.GetPrimAtPath(d.GetRelationship("thermal:ceilingJetTarget").GetTargets()[0])
    c, half = d.GetAttribute("thermal:ceilingJetCenter").Get(), d.GetAttribute("thermal:ceilingJetHalfSize").Get()
    tc_ = ser.get(ceil, "thermal:surfaceTemperature", tc)
    dt = jet_delta(c[0] + half + offset, c[1], ser.get(d, "thermal:surfaceTemperature", tc), tc_,
                   ser.get(d, "thermal:ceilingJetStrength", tc), ser.get(d, "thermal:ceilingJetDecayLength", tc), (c[0], c[1]), half)
    return ceil, tc_ + float(dt)


def metrics(usd, cam=(0.95, 20.0)):
    st = Usd.Stage.Open(str(usd)); ser = Series(st); labs = labels(st)
    out = {}
    for key, _, a, b, frame in METRICS:
        tc = labs.index(frame)
        if a == "jet":
            ceil, t = jet_point(st, ser, tc)
            ta = apparent(st, ser, ceil, tc, cam, t)
        else:
            ta = apparent(st, ser, prim(st, a), tc, cam)
        out[key] = ta - apparent(st, ser, prim(st, b), tc, cam)
    return out


def main():
    logging.basicConfig(level=logging.WARNING)
    root = ROOT / "case_studies" / "B_hvac_system" / "sensitivity"
    base_usd = pipeline.build(CASE, overrides={"output_dir": str(root / "base")})
    base = metrics(base_usd)
    rows = []
    for label, path, lo, hi, default in PARAMS:
        res = {}
        for tag, val in (("low", lo), ("high", hi)):
            if path[0] == "camera":
                cam = (val, 20.0) if path[1] == "emissivity_setting" else (0.95, val)
                res[tag] = metrics(base_usd, cam)
            else:
                usd = pipeline.build(CASE, overrides={**nest(path, val), "output_dir": str(root / f"{path[-1]}_{tag}")})
                res[tag] = metrics(usd)
        rows.append(dict(parameter=label, low=lo, high=hi, default=default, results=res))
        print(label, {m: (round(res["low"][m], 2), round(res["high"][m], 2)) for m in base})
    (ROOT / "case_studies" / "sensitivity.json").write_text(encoding="utf-8", data=json.dumps(
        dict(metrics={k: d for k, d, *_ in METRICS}, base=base, rows=rows), indent=2, ensure_ascii=False))
    keys = [k for k, *_ in METRICS]
    lines = ["| Parameter (low – high) | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    lines.append("| **Default** | " + " | ".join(f"**{base[m]:+.1f}**" for m in keys) + " |")
    for r in rows:
        cells = []
        for m in keys:
            lo, hi = r["results"]["low"][m], r["results"]["high"][m]
            cells.append("—" if abs(lo - base[m]) < 0.05 and abs(hi - base[m]) < 0.05 else f"{lo:+.1f} … {hi:+.1f}")
        lines.append(f"| {r['parameter']} ({r['low']} – {r['high']}) | " + " | ".join(cells) + " |")
    legend = "\n".join(f"- **{k}**: {d}" for k, d, *_ in METRICS)
    (ROOT / "docs" / "sensitivity.md").write_text(encoding="utf-8", data=
        "# Sensitivity of apparent thermal contrasts (case B)\n\nApparent temperature contrast (K) as read by a camera with "
        "emissivity setting 0.95 and reflected-temperature setting 20 °C (unless that setting is the parameter varied); "
        "one parameter varied at a time between illustrative bounds. — = change below 0.05 K.\n\n" + legend + "\n\n"
        + "\n".join(lines) + "\n")
    print("base", base)


if __name__ == "__main__":
    main()
