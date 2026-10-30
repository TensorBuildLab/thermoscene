"""Generality check: trace the HVAC system serving every conditioned zone of EnergyPlus example models.

Writes docs/topology_survey.md. Run:  python case_studies/topology_survey.py
"""
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene.energyplus.runner import energyplus_dir, find_energyplus  # noqa: E402
from thermoscene.energyplus.topology import HVACGraph  # noqa: E402
from thermoscene.pipeline import load_model  # noqa: E402

MODELS = ["5ZoneAirCooled", "RefBldgMediumOfficeNew2004_Chicago", "RefBldgLargeOfficeNew2004_Chicago",
          "RefBldgSmallOfficeNew2004_Chicago", "RefBldgOutPatientNew2004_Chicago", "RefBldgMidriseApartmentNew2004_Chicago",
          "FanCoilAutoSize", "PackagedTerminalHeatPump", "HVACTemplate-5ZonePackagedVAV", "HVACTemplate-5ZoneVAVWaterCooled",
          "HVACTemplate-5ZoneFanCoil", "HVACTemplate-5ZonePTAC"]
KEY = ["VAVTerminalUnit", "AirTerminalUnit", "FanCoilUnit", "PackagedTerminalUnit", "Fan", "CoolingCoil", "DXCoolingCoil",
       "HeatingCoil", "HotWaterCoil", "OutdoorAirMixer", "Chiller", "Boiler", "Pump", "CoolingTower"]


def main():
    exe = find_energyplus()
    ex = energyplus_dir(exe) / "ExampleFiles"
    rows = ["| Model | Zones (conditioned) | HVAC components | Traced zones | Mean chain length | Component classes found (all zones) |",
            "|---|---|---|---|---|---|"]
    total, traced = 0, 0
    for name in MODELS:
        f = ex / f"{name}.idf"
        if not f.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                m = load_model(f, exe, tmp)
                g = HVACGraph(m)
            except Exception as e:  # noqa: BLE001
                rows.append(f"| {name} | error: {e} | | | | |"); continue
            ok, lengths, classes = 0, [], Counter()
            for z in g.zone_inlets:
                try:
                    s = g.serving(z)
                except KeyError:
                    continue
                if s["components"]:
                    ok += 1; lengths.append(len(s["components"]))
                    classes.update({c.cls for c, _ in s["components"]})
            total += len(g.zone_inlets); traced += ok
            found = ", ".join(f"{k} ({classes[k]})" for k in KEY if classes[k])
            rows.append(f"| {name} | {len(m.zone_names())} ({len(g.zone_inlets)}) | {len(g.comps)} | {ok}/{len(g.zone_inlets)} | "
                        f"{sum(lengths) / max(len(lengths), 1):.1f} | {found} |")
            print(rows[-1])
    out = ROOT / "docs" / "topology_survey.md"
    out.write_text(encoding="utf-8", data="# Topology survey\n\nThe generic tracer (`thermoscene.energyplus.topology`) applied, without any model-specific "
                   f"configuration, to EnergyPlus example models. **{traced} of {total} conditioned zones traced.** *Traced zones*: "
                   "conditioned zones for which a non-empty serving chain was recovered. Numbers in parentheses: how many traced "
                   "zones contain that class.\n\n" + "\n".join(rows) + "\n")
    print(f"{traced}/{total} conditioned zones traced; wrote", out)


if __name__ == "__main__":
    main()
