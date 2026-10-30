"""Robustness: build a scene for EVERY conditioned zone of the unmodified DOE reference medium office.

Re-uses one EnergyPlus run (simulation cache). Writes docs/zone_sweep.md and case_studies/zone_sweep.json.
Run:  python case_studies/zone_sweep.py
"""
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402

CASE = ROOT / "case_studies" / "C_doe_medium_office" / "case.yaml"
ZONES = ["Core_bottom", "Core_mid", "Core_top"] + [f"Perimeter_{f}_ZN_{i}" for f in ("bot", "mid", "top") for i in range(1, 5)]


def main():
    logging.basicConfig(level=logging.WARNING)
    rows = []
    for z in ZONES:
        out = ROOT / "case_studies" / "C_doe_medium_office" / "sweep" / z
        t0 = time.time()
        try:
            usd = pipeline.build(CASE, overrides={"scene": {"zone": z}, "faults": [], "cameras": [], "output_dir": str(out)})
            s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            cls = [e["cls"] for e in s["elements"]]
            prov = {}
            for k, v in s["provenance_counts"].items():
                if ".surfaceTemperature:" in k:
                    prov[k.split(":")[-1]] = prov.get(k.split(":")[-1], 0) + v
            rows.append(dict(zone=z, ok=True, plenum=s["plenum"], surfaces=s["counts"]["surfaces"],
                             elements=s["counts"]["elements"], chain=len(s["serving_chain"]),
                             diffusers=cls.count("Diffuser"), grilles=cls.count("ReturnGrille"),
                             plenum_elements=sum(1 for e in s["elements"] if e["id"].startswith(s["plenum"] or "-")),
                             rooftop_elements=sum(1 for e in s["elements"] if e["id"].startswith("Outdoors/")),
                             provenance=prov, seconds=round(time.time() - t0, 1), usd_mb=round(usd.stat().st_size / 1e6, 2)))
        except Exception as e:  # noqa: BLE001
            rows.append(dict(zone=z, ok=False, error=f"{type(e).__name__}: {e}"))
        print(rows[-1])
    (ROOT / "case_studies" / "zone_sweep.json").write_text(encoding="utf-8", data=json.dumps(rows, indent=2))
    lines = ["| Zone | Plenum | EnergyPlus surfaces | Traced components | Diffusers / grilles | Plenum elements | Rooftop elements | "
             "Surface temperatures sim / phys / heur | Build time (s) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r["ok"]:
            lines.append(f"| {r['zone']} | failed: {r['error']} | | | | | | | |"); continue
        p = r["provenance"]
        lines.append(f"| {r['zone']} | {r['plenum']} | {r['surfaces']} | {r['chain']} | {r['diffusers']} / {r['grilles']} | "
                     f"{r['plenum_elements']} | {r['rooftop_elements']} | {p.get('simulated', 0)} / {p.get('physics', 0)} / "
                     f"{p.get('heuristic', 0)} | {r['seconds']} |")
    ok = sum(r["ok"] for r in rows)
    (ROOT / "docs" / "zone_sweep.md").write_text(encoding="utf-8", data=
        f"# Zone sweep: DOE reference medium office\n\nOne scene per conditioned zone of the unmodified model "
        f"(`RefBldgMediumOfficeNew2004_Chicago.idf`), one shared EnergyPlus run. **{ok} / {len(rows)} zones built.** "
        f"Rooftop equipment is generated only where the scene contains roof surfaces (top floor).\n\n" + "\n".join(lines) + "\n")
    print(f"{ok}/{len(rows)} zones built")


if __name__ == "__main__":
    main()
