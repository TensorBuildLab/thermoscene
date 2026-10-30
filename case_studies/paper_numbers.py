"""Every number quoted in the text of the paper's Sections 5.1-5.6, computed from the built case studies.

Run after building A, B and C with --render:
    python case_studies/paper_numbers.py            -> case_studies/paper_numbers.json, docs/paper_numbers.md
    python case_studies/paper_numbers.py --no-cost  (skip the timing and storage measurements, which rebuild C)

Temperatures are true surface temperatures (deg C) unless a line says "apparent". Each entry states its definition,
so a changed number in the paper can be traced to a changed simulation rather than to a changed definition.
"""
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from pxr import Usd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402
from thermoscene.render.raycast import accel, raycast, usd_faces  # noqa: E402
from thermoscene.render.shading import Series, camera_rays, cameras, labels, thermal_frame, thermal_prims  # noqa: E402

CS = ROOT / "case_studies"
T, TF, MF, AIR = "thermal:surfaceTemperature", "mep:fluidTemperature", "mep:fluidMassFlow", "thermal:airTemperature"
W = lambda h: f"WINTER 01-15 {h}"           # second (reported) day of each run period
S = lambda h: f"SUMMER 07-16 {h}"


class Stage:
    def __init__(self, case):
        self.st = Usd.Stage.Open(str(CS / case / "output" / f"{case}.usda"))
        self.ser, self.labs = Series(self.st), labels(self.st)

    def prim(self, path_or_cls, under="/World"):
        if path_or_cls.startswith("/"):
            p = self.st.GetPrimAtPath(path_or_cls)
            if not p:
                raise KeyError(path_or_cls)
            return p
        return next(p for p in Usd.PrimRange(self.st.GetPrimAtPath(under)) if p.HasAttribute(T)
                    and p.GetAttribute("semantic:class").Get() == path_or_cls)

    def get(self, what, label, attr=T, under="/World"):
        return float(self.ser.get(self.prim(what, under), attr, self.labs.index(label)))

    def day(self, prefix):
        return [lab for lab in self.labs if lab.startswith(prefix)]


def r1(x):
    return round(float(x), 1)


def d1(a, b):
    """Difference of two values as quoted in the text: from the rounded values, so that 'X K (a vs b)' adds up."""
    return round(r1(a) - r1(b), 1)


def case_b(out):
    b = Stage("B_hvac_system")
    O, P, X = "/World/Office", "/World/Plenum/Elements", "/World/Outdoors"
    defect, wall = O + "/Surfaces/TS_DEFECT_Insulation_Void", O + "/Surfaces/South_Wall"
    diff, ceil, disch = O + "/Elements/Supply_Diffuser_1", O + "/Surfaces/Ceiling", P + "/Discharge_Duct_1"
    valve, pipe, gap = P + "/HW_Control_Valve", P + "/HW_Supply_Pipe", P + "/Duct_Insulation_Gap"
    chw = X + "/Elements/Rooftop_Chiller_Central_AHU_Cooling__Supply_Pipe"
    n = out.setdefault("B", {})
    for lab in (W("07:00"), W("15:00"), S("15:00")):
        n[f"envelope {lab}: defect / wall / difference"] = [r1(b.get(defect, lab)), r1(b.get(wall, lab)),
                                                            d1(b.get(defect, lab), b.get(wall, lab))]
    for lab in (W("22:00"), W("07:30"), W("09:00"), S("15:00")):
        n[f"diffuser {lab}: face / ceiling / difference / supply air (zone inlet node)"] = [
            r1(b.get(diff, lab)), r1(b.get(ceil, lab)), d1(b.get(diff, lab), b.get(ceil, lab)), r1(b.get(disch, lab, TF))]
    win = b.day("WINTER 01-15")
    tv, tmax = max((b.get(valve, lab), lab) for lab in win)
    n["reheat valve, winter day maximum: valve / time / HW pipe jacket at that time"] = [r1(tv), tmax, r1(b.get(pipe, tmax))]
    n[f"reheat valve {W('10:00')} (reheat off, trapped water)"] = r1(b.get(valve, W("10:00")))
    gmax, gl = max((b.get(gap, lab) - b.get(disch, lab), lab) for lab in win)
    n["duct gap - wrapped duct, winter day maximum (reheat): difference / time"] = [r1(gmax), gl]
    n[f"duct gap - wrapped duct {S('15:00')}"] = r1(b.get(gap, S("15:00")) - b.get(disch, S("15:00")))
    lab = S("15:00")
    t_out = b.get(X, lab, AIR)
    n[f"roof {lab}: outdoor air / roof membrane / condenser fan / fan - air"] = [
        r1(t_out), r1(b.get("RoofSurface", lab, under=X)), r1(b.get("CondenserFan", lab, under=X)),
        d1(b.get("CondenserFan", lab, under=X), t_out)]
    summ = b.day("SUMMER 07-16")
    flowing = [b.get(chw, x, TF) for x in summ if b.get(chw, x, MF) > 0]
    n["chilled-water supply, summer day: setpoint (minimum while flowing) / at 15:00 / maximum while flowing"] = [
        r1(min(flowing)), r1(b.get(chw, S("15:00"), TF)), r1(max(flowing))]
    n[f"roof membrane - outdoor air {W('03:00')} (sky radiation)"] = r1(b.get("RoofSurface", W("03:00"), under=X) - b.get(X, W("03:00"), AIR))
    return n


def case_c(out):
    c = Stage("C_doe_medium_office")
    Z, X = "/World/Perimeter_top_ZN_1", "/World/Outdoors"
    n = out.setdefault("C", {})
    lab = W("03:00")
    n[f"winter night {lab}: outdoor air / zone air (setback) / defect / wall / difference"] = [
        r1(c.get(X, lab, AIR)), r1(c.get(Z, lab, AIR)), r1(c.get(Z + "/Surfaces/TS_DEFECT_Insulation_Void", lab)),
        r1(c.get(Z + "/Surfaces/Perimeter_top_ZN_1_Wall_South", lab)),
        d1(c.get(Z + "/Surfaces/TS_DEFECT_Insulation_Void", lab), c.get(Z + "/Surfaces/Perimeter_top_ZN_1_Wall_South", lab))]
    n[f"roof outside face (EnergyPlus) - outdoor air {lab}"] = r1(c.get(X + "/Surfaces/Building_Roof__exterior_", lab) - c.get(X, lab, AIR))
    diff, ceil = Z + "/Elements/Supply_Diffuser_1", Z + "/Surfaces/Perimeter_top_ZN_1_Ceiling"
    for lab in (W("07:00"), S("15:00")):
        n[f"diffuser {lab}: face / ceiling / difference"] = [r1(c.get(diff, lab)), r1(c.get(ceil, lab)),
                                                            d1(c.get(diff, lab), c.get(ceil, lab))]
    s = json.loads((CS / "C_doe_medium_office/output/summary.json").read_text(encoding="utf-8"))
    els = s["elements"]
    dif = [e for e in els if e["cls"] == "Diffuser"]
    n["diffusers / return grilles / discharge run size"] = [len(dif), sum(e["cls"] == "ReturnGrille" for e in els),
                                                            next(e["size"] for e in els if e["id"].endswith("Discharge_Duct_1"))]
    n["zone design flow (sum of diffuser flows, m3/s)"] = round(sum(float(e["size"].split(", ")[1].split()[0]) for e in dif), 2)
    n["rooftop unit / condenser sizes"] = [next(e["size"] for e in els if e["id"] == "Outdoors/AHU_Discharge"),
                                           next(e["size"] for e in els if e["id"] == "Outdoors/AHU_Condenser")]
    return n


def provenance(out):
    rows = {}
    for case, tag in (("B_hvac_system", "B"), ("C_doe_medium_office", "C")):
        for f in sorted((CS / case / "output/renders").glob("*/provenance_share.json")):
            rows[f"{tag} {f.parent.name}"] = {k: round(100 * v, 1) for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
    out["provenance_share_percent"] = rows


def counts(out):
    out["counts"] = {}
    for case in ("A_single_room", "B_hvac_system", "C_doe_medium_office"):
        s = json.loads((CS / case / "output/summary.json").read_text(encoding="utf-8"))
        lv = {}
        for k, v in s["provenance_counts"].items():
            if ".surfaceTemperature:" in k:
                lv[k.split(":")[-1]] = lv.get(k.split(":")[-1], 0) + v
        out["counts"][case] = dict(s["counts"], surface_temperature_provenance=lv, energyplus=s["energyplus"])


def render_cost(case):
    """Seconds for the one-off ray cast of each camera and mean seconds per further frame (shading only)."""
    st = Usd.Stage.Open(str(CS / case / "output" / f"{case}.usda"))
    ser, labs = Series(st), labels(st)
    prims = thermal_prims(st)
    meshes = accel([usd_faces(p) for p in prims])
    res = {}
    for cam in cameras(st):
        t0 = time.perf_counter()
        o, d, (h, w) = camera_rays(cam)
        hit, t = raycast(o, d.reshape(-1, 3), meshes)
        owner = np.where(hit >= 0, hit, len(prims)).reshape(h, w)
        hits = o + d * np.where(np.isfinite(t), t, 0).reshape(h, w, 1)
        t_cast = time.perf_counter() - t0
        frames = list(cam.GetAttribute("thermal:previewFrames").Get() or [])
        t0 = time.perf_counter()
        for lab in frames:
            thermal_frame(st, ser, labs.index(lab), prims, owner, hits, cam, seed=0)
        res[cam.GetName()] = dict(raycast_s=round(t_cast, 2), per_frame_s=round((time.perf_counter() - t0) / max(len(frames), 1), 3),
                                  resolution=f"{w}x{h}")
    return res


def cost(out):
    c = out["cost"] = {}
    for case in ("B_hvac_system", "C_doe_medium_office"):
        c[f"render {case}"] = render_cost(case)
        s = json.loads((CS / case / "output/summary.json").read_text(encoding="utf-8"))
        c[f"build {case}, as last built (s)"] = dict(seconds=s["build_seconds"], energyplus_cached=s["energyplus"].get("cached"))
        with tempfile.TemporaryDirectory() as tmp:      # rebuild with the EnergyPlus run taken from the cache
            t0 = time.perf_counter()
            pipeline.build(CS / case / "case.yaml", overrides={"output_dir": tmp})
            c[f"build {case}, cached EnergyPlus run (s)"] = round(time.perf_counter() - t0, 1)
    c["B stage size, time samples (MB)"] = round((CS / "B_hvac_system/output/B_hvac_system.usda").stat().st_size / 1e6, 2)
    co = CS / "C_doe_medium_office/output"
    c["C stage size, sidecar: usda + npz (MB)"] = round(((co / "C_doe_medium_office.usda").stat().st_size
                                                         + (co / "C_doe_medium_office.series.npz").stat().st_size) / 1e6, 2)
    with tempfile.TemporaryDirectory() as tmp:          # same scene with time samples instead of the sidecar
        usd = pipeline.build(CS / "C_doe_medium_office/case.yaml",
                             overrides={"storage": {"time_series": "timesamples"}, "output_dir": tmp})
        c["C stage size, time samples (MB)"] = round(usd.stat().st_size / 1e6, 1)


def markdown(out):
    lines = ["# Numbers quoted in the paper's text", "",
             "Generated by `python case_studies/paper_numbers.py` from the built case studies "
             f"({out['engine']}). Temperatures in °C, differences in K; true surface temperatures unless stated. "
             "Times are on the second (reported) day of each run period.", ""]
    for sec, title in (("B", "Case study B (Section 5.1)"), ("C", "Case study C (Section 5.2)")):
        lines += [f"## {title}", "", "| Quantity | Value |", "|---|---|"]
        lines += [f"| {k} | {' / '.join(map(str, v)) if isinstance(v, list) else v} |" for k, v in out[sec].items()]
        lines.append("")
    lines += ["## Pixel provenance (Section 5.4, Fig. 8), % of pixels", "", "| Camera | Shares |", "|---|---|"]
    lines += [f"| {k} | {', '.join(f'{a} {b}' for a, b in v.items())} |" for k, v in out["provenance_share_percent"].items()]
    lines += ["", "## Counts (Section 5.1-5.2, README)", "", "| Case | Surfaces | Elements | Components | Surface temperatures by provenance |",
              "|---|---|---|---|---|"]
    lines += [f"| {k} | {v['surfaces']} | {v['elements']} | {v['components']} | "
              f"{', '.join(f'{a} {b}' for a, b in sorted(v['surface_temperature_provenance'].items()))} |" for k, v in out["counts"].items()]
    if "cost" in out:
        lines += ["", "## Cost (Section 5.6)", "", "| Quantity | Value |", "|---|---|"]
        for k, v in out["cost"].items():
            if isinstance(v, dict) and all(isinstance(x, dict) for x in v.values()):
                v = "; ".join(f"{cam}: ray cast {d['raycast_s']} s, {d['per_frame_s']} s/frame ({d['resolution']})" for cam, d in v.items())
            elif isinstance(v, dict):
                v = ", ".join(f"{a} {b}" for a, b in v.items())
            lines.append(f"| {k} | {v} |")
    (ROOT / "docs" / "paper_numbers.md").write_text(encoding="utf-8", data="\n".join(lines) + "\n")


def main():
    logging.basicConfig(level=logging.WARNING)
    st = Usd.Stage.Open(str(CS / "B_hvac_system/output/B_hvac_system.usda"))
    out = {"engine": str(st.GetRootLayer().customLayerData.get("ts:source", "")).split(";")[0] or "EnergyPlus"}
    case_b(out); case_c(out); provenance(out); counts(out)
    if "--no-cost" not in sys.argv:
        cost(out)
    (CS / "paper_numbers.json").write_text(encoding="utf-8", data=json.dumps(out, indent=2, ensure_ascii=False))
    markdown(out)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
