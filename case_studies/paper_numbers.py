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


def case_sql(case):
    """The cached EnergyPlus SQLite of a built case (same key as pipeline._cached_simulation)."""
    import hashlib
    from thermoscene import config as config_mod
    cfg = config_mod.load(CS / case / "case.yaml")
    h = hashlib.sha256()
    for f in (CS / case / "output" / "prepared.idf", cfg["model"]["epw"]):
        h.update(Path(f).read_bytes())
    h.update(str(cfg["energyplus"]["executable"]).encode())
    sql = CS / case / ".sim_cache" / h.hexdigest()[:16] / "eplusout.sql"
    if not sql.exists():
        raise FileNotFoundError(f"{sql}: rebuild {case} first")
    return sql, cfg


def envelope_energy(case, wall, defect):
    """Daily conduction through the inside face of the host wall with the injected void, against the same wall
    without it (the void's area at the insulated wall's flux). Wh per day; negative = heat leaving the room."""
    from thermoscene.energyplus.results import Results
    sql, _ = case_sql(case)
    r = Results(sql)
    dt = r.step_minutes / 60
    var, var_a = "Surface Inside Face Conduction Heat Transfer Rate", "Surface Inside Face Conduction Heat Transfer Rate per Area"
    qw, qd = r.series(var_a, wall), r.series(var_a, defect)
    area = lambda key, q: float(np.median([p / a for p, a in zip(r.series(var, key), q) if abs(a) > 0.5]))
    a_wall, a_def = area(wall, qw), area(defect, qd)
    res = {"void area (m2) / insulated wall area (m2)": [round(a_def, 2), round(a_wall, 2)]}
    for day in ("WINTER 01-15", "SUMMER 07-16"):
        i = [k for k, lab in enumerate(r.labels) if lab.startswith(day)]
        e_def = float(np.sum(qd[i])) * dt                      # Wh/m2 through the void
        e_ins = float(np.sum(qw[i])) * dt                      # Wh/m2 through the insulated wall
        with_void = a_wall * e_ins + a_def * e_def
        without = (a_wall + a_def) * e_ins
        res[f"{day}: void / insulated wall (Wh/m2 per day); ratio"] = [round(e_def, 1), round(e_ins, 1), round(e_def / e_ins, 1)]
        res[f"{day}: whole wall without / with void (Wh per day); change (%)"] = [round(without), round(with_void),
                                                                                  round(100 * (with_void - without) / abs(without), 1)]
    return res


def duct_gap_energy():
    """Heat picked up by the supply air through the unwrapped segment, minus what the same segment would pick up if
    wrapped (the pipeline's insulated_flat and bare_duct relations), against the office's sensible cooling
    m_dot c_p (T_zone - T_supply). Counted only while air flows. Per zone instance."""
    import yaml
    th = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))["thermal"]
    h_i, h_o, k = th["film_coefficients"]["inside_air"], th["film_coefficients"]["plenum"], th["insulation"]["conductivity"]
    delta = th["insulation"]["duct_wrap_m"]
    s = json.loads((CS / "B_hvac_system/output/summary.json").read_text(encoding="utf-8"))
    size = {e["id"]: e["size"] for e in s["elements"]}
    d = float(size["Plenum/Discharge_Duct_1"].split()[0]) / 1000
    length = float(size["Plenum/Duct_Insulation_Gap"].split()[0])
    area = np.pi * d * length
    b = Stage("B_hvac_system")
    duct, plenum, office = "/World/Plenum/Elements/Discharge_Duct_1", "/World/Plenum", "/World/Office"
    res = {"unwrapped area (m2)": round(area, 3)}
    for day in ("WINTER 01-15", "SUMMER 07-16"):
        gain, cool = [], []
        for lab in b.day(day):
            m = b.get(duct, lab, MF)
            if m <= 0:
                continue
            tf, ta, tz = b.get(duct, lab, TF), b.get(plenum, lab, AIR), b.get(office, lab, AIR)
            gain.append(area * (ta - tf) * (1 / (1 / h_i + 1 / h_o) - 1 / (1 / h_i + delta / k + 1 / h_o)))
            cool.append(m * 1005.0 * (tz - tf))
        dt = 0.25
        lab = "SUMMER 07-16 15:00"
        if day.startswith("SUMMER"):
            m, tf = b.get(duct, lab, MF), b.get(duct, lab, TF)
            g15 = area * (b.get(plenum, lab, AIR) - tf) * (1 / (1 / h_i + 1 / h_o) - 1 / (1 / h_i + delta / k + 1 / h_o))
            c15 = m * 1005.0 * (b.get(office, lab, AIR) - tf)
            res["SUMMER 15:00: extra heat gain (W) / office sensible cooling (W); share (%)"] = [round(g15, 1), round(c15), round(100 * g15 / c15, 2)]
        res[f"{day}: extra heat gain (Wh per day) / office sensible cooling (Wh per day); share (%)"] = [
            round(sum(gain) * dt, 1), round(sum(cool) * dt), round(100 * sum(gain) / sum(cool), 2) if sum(cool) else None]
    return res


def energy(out):
    out["energy"] = {"B envelope void": envelope_energy("B_hvac_system", "SOUTH_WALL", "TS_DEFECT_INSULATION_VOID"),
                     "C envelope void": envelope_energy("C_doe_medium_office", "PERIMETER_TOP_ZN_1_WALL_SOUTH",
                                                        "TS_DEFECT_INSULATION_VOID"),
                     "B duct insulation gap": duct_gap_energy()}


def provenance(out):
    rows = {}
    for case, tag in (("B_hvac_system", "B"), ("C_doe_medium_office", "C")):
        for f in sorted((CS / case / "output/renders").glob("*/provenance_share.json")):
            js = json.loads(f.read_text(encoding="utf-8"))
            frames = js["frames"].values()
            rows[f"{tag} {f.parent.name}"] = {k: [round(100 * v, 1), round(100 * min(s.get(k, 0) for s in frames), 1),
                                                  round(100 * max(s.get(k, 0) for s in frames), 1)]
                                              for k, v in js["mean"].items()}
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
    lines += ["## Energy relevance of the injected faults", "",
              "Envelope voids: EnergyPlus inside-face conduction (negative = heat leaving the room). Duct gap: heat "
              "gained through the unwrapped segment beyond what the wrapped segment would gain, while air flows, "
              "against the office's sensible cooling (one zone instance).", "", "| Quantity | Value |", "|---|---|"]
    for grp, vals in out["energy"].items():
        lines += [f"| {grp}: {k} | {' / '.join(map(str, v)) if isinstance(v, list) else v} |" for k, v in vals.items()]
    lines.append("")
    lines += ["## Pixel provenance (Section 5.4, Fig. 8), % of pixels: mean over the camera's frames (min–max)", "",
              "| Camera | Shares |", "|---|---|"]
    lines += [f"| {k} | {', '.join(f'{a} {b[0]} ({b[1]}–{b[2]})' for a, b in v.items())} |"
              for k, v in out["provenance_share_percent"].items()]
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
    case_b(out); case_c(out); energy(out); provenance(out); counts(out)
    if "--no-cost" not in sys.argv:
        cost(out)
    (CS / "paper_numbers.json").write_text(encoding="utf-8", data=json.dumps(out, indent=2, ensure_ascii=False))
    markdown(out)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
