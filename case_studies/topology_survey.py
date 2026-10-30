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


# ---------------------------------------------------------------- reference chain from the model's declarations
# The tracer walks the node graph. The reference below reads only the objects that DECLARE the system structure:
# the zone's equipment list, the air distribution unit and its terminal, the air loop whose supply path feeds the
# zone's terminal, that loop's supply branches, outdoor-air equipment list, supply path and return path, and, for every
# water-side component, the supply side of the plant/condenser loop whose demand branches contain it (plus the demand
# inlet branch and the components ahead of it on its own branch). Child components come from <x>_object_type /
# <x>_name field pairs (coils inside terminals, fan coils and CoilSystem containers). Nothing is matched by name.
def _refs(f):
    out = []
    for k, v in f.items():
        if k.endswith("_object_type") and isinstance(v, str) and isinstance(f.get(k[:-12] + "_name"), str):
            out.append((v, f[k[:-12] + "_name"]))
    return out


def declared_chain(m, zone):
    from thermoscene.energyplus.topology import _excluded
    key = lambda t, n: f"{t}:{n}".upper()
    truth, water = set(), []

    def add(t, n, depth=0):
        _, f = m.obj(t, n)
        if f is None or depth > 4:
            return
        if not _excluded(t):
            truth.add(key(t, n))
            if any("water" in k and "inlet_node" in k for k in f) or t.startswith(("Chiller", "Boiler")):
                water.append((t, n))
        for ct, cn in _refs(f):
            add(ct, cn, depth + 1)

    def branch_comps(bname):
        _, b = m.obj("Branch", bname)
        return [(c["component_object_type"], c["component_name"]) for c in (b or {}).get("components", [])]

    nodelists = {n.upper(): [x["node_name"].upper() for x in f.get("nodes", [])] for n, f in m.objs("NodeList").items()}

    def expand(name):
        return nodelists.get(name.upper(), [name.upper()]) if name else []

    conn = next((f for f in m.objs("ZoneHVAC:EquipmentConnections").values() if f["zone_name"].upper() == zone.upper()), None)
    if conn is None:
        return truth
    zone_inlets = set(expand(conn.get("zone_air_inlet_node_or_nodelist_name")))
    _, eql = m.obj("ZoneHVAC:EquipmentList", conn.get("zone_conditioning_equipment_list_name", ""))
    feed = set(zone_inlets)                                   # nodes through which an air loop can reach the zone
    for item in (eql or {}).get("equipment", []):
        t, n = item["zone_equipment_object_type"], item["zone_equipment_name"]
        if t == "ZoneHVAC:AirDistributionUnit":
            _, adu = m.obj(t, n)
            tt, tn = adu["air_terminal_object_type"], adu["air_terminal_name"]
            add(tt, tn)
            _, term = m.obj(tt, tn)
            feed |= {v.upper() for k, v in (term or {}).items() if isinstance(v, str) and k.endswith("inlet_node_name")}
        else:
            add(t, n)
            _, zf = m.obj(t, n)
            feed |= {v.upper() for k, v in (zf or {}).items() if isinstance(v, str) and k.endswith("inlet_node_name")}
    for lname, loop in m.objs("AirLoopHVAC").items():
        demand_in = set(expand(loop.get("demand_side_inlet_node_names")))
        paths = [p for p in m.objs("AirLoopHVAC:SupplyPath").values() if p["supply_air_path_inlet_node_name"].upper() in demand_in]
        comps = [(c["component_object_type"], c["component_name"]) for p in paths for c in p.get("components", [])]
        outlets = set()
        for t, n in comps:
            _, f = m.obj(t, n)
            outlets |= {x["outlet_node_name"].upper() for x in (f or {}).get("nodes", []) if "outlet_node_name" in x}
            outlets |= {v.upper() for k, v in (f or {}).items() if isinstance(v, str) and k.startswith("outlet_node")}
        if not (outlets & feed) and not (demand_in & feed):
            continue
        for t, n in comps:
            add(t, n)
        for rp in m.objs("AirLoopHVAC:ReturnPath").values():
            if rp["return_air_path_outlet_node_name"].upper() == loop.get("demand_side_outlet_node_name", "").upper():
                for c in rp.get("components", []):
                    add(c["component_object_type"], c["component_name"])
        _, bl = m.obj("BranchList", loop["branch_list_name"])
        for b in (bl or {}).get("branches", []):
            for t, n in branch_comps(b["branch_name"]):
                if t == "AirLoopHVAC:OutdoorAirSystem":
                    _, oas = m.obj(t, n)
                    _, oel = m.obj("AirLoopHVAC:OutdoorAirSystem:EquipmentList", (oas or {}).get("outdoor_air_equipment_list_name", ""))
                    for ct, cn in _refs(oel or {}):
                        add(ct, cn)
                else:
                    add(t, n)
    # plant and condenser loops feeding water-side components (repeat: chillers pull in condenser loops)
    done = set()
    while water:
        t, n = water.pop()
        if key(t, n) in done:
            continue
        done.add(key(t, n))
        for cls, sup_bl, dem_bl, dem_cl in (("PlantLoop", "plant_side_branch_list_name", "demand_side_branch_list_name",
                                             "demand_side_connector_list_name"),
                                            ("CondenserLoop", "condenser_side_branch_list_name", "condenser_demand_side_branch_list_name",
                                             "condenser_demand_side_connector_list_name")):
            for loop in m.objs(cls).values():
                _, dbl = m.obj("BranchList", loop.get(dem_bl) or loop.get("demand_side_branch_list_name", ""))
                hit = None
                for b in (dbl or {}).get("branches", []):
                    bc = branch_comps(b["branch_name"])
                    if key(t, n) in [key(*c) for c in bc]:
                        hit = bc[:[key(*c) for c in bc].index(key(t, n))]
                if hit is None:
                    continue
                for c in hit:
                    add(*c)
                _, cl = m.obj("ConnectorList", loop.get(dem_cl) or loop.get("demand_side_connector_list_name", ""))
                for ct, cn in _refs(cl or {}):
                    if ct == "Connector:Splitter":
                        _, sp = m.obj(ct, cn)
                        for c in branch_comps((sp or {}).get("inlet_branch_name", "")):
                            add(*c)
                _, sbl = m.obj("BranchList", loop.get(sup_bl, ""))
                for b in (sbl or {}).get("branches", []):
                    for c in branch_comps(b["branch_name"]):
                        add(*c)
    return truth


def main():
    exe = find_energyplus()
    ex = energyplus_dir(exe) / "ExampleFiles"
    rows = ["| Model | Zones (conditioned) | HVAC components | Traced zones | Mean chain length | Precision / recall vs declared structure | Exact zones | Component classes found (all zones) |",
            "|---|---|---|---|---|---|---|---|"]
    total, traced, TP, FP, FN, exact_all = 0, 0, 0, 0, 0, 0
    mismatches = []
    for name in MODELS:
        f = ex / f"{name}.idf"
        if not f.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                m = load_model(f, exe, tmp)
                g = HVACGraph(m)
            except Exception as e:  # noqa: BLE001
                rows.append(f"| {name} | error: {e} | | | | | | |"); continue
            ok, lengths, classes, tp, fp, fn, exact = 0, [], Counter(), 0, 0, 0, 0
            for z in g.zone_inlets:
                try:
                    s = g.serving(z)
                except KeyError:
                    continue
                if s["components"]:
                    ok += 1; lengths.append(len(s["components"]))
                    classes.update({c.cls for c, _ in s["components"]})
                found = {c.id for c, _ in s["components"]}
                ref = declared_chain(m, z)
                tp += len(found & ref); fp += len(found - ref); fn += len(ref - found)
                exact += found == ref
                if found != ref:
                    mismatches.append((name, z, sorted(found - ref), sorted(ref - found)))
            total += len(g.zone_inlets); traced += ok; TP += tp; FP += fp; FN += fn; exact_all += exact
            found_cls = ", ".join(f"{k} ({classes[k]})" for k in KEY if classes[k])
            rows.append(f"| {name} | {len(m.zone_names())} ({len(g.zone_inlets)}) | {len(g.comps)} | {ok}/{len(g.zone_inlets)} | "
                        f"{sum(lengths) / max(len(lengths), 1):.1f} | {tp / max(tp + fp, 1):.3f} / {tp / max(tp + fn, 1):.3f} | "
                        f"{exact}/{len(g.zone_inlets)} | {found_cls} |")
            print(rows[-1])
    prec, rec = TP / max(TP + FP, 1), TP / max(TP + FN, 1)
    detail = ""
    if mismatches:
        detail = "\n\n## Zones whose traced chain differs from the declared structure\n\n| Model | Zone | Traced, not declared | Declared, not traced |\n|---|---|---|---|\n" + \
            "\n".join(f"| {a} | {b} | {', '.join(c) or '—'} | {', '.join(d) or '—'} |" for a, b, c, d in mismatches)
    out = ROOT / "docs" / "topology_survey.md"
    out.write_text(encoding="utf-8", data="# Topology survey\n\nThe generic tracer (`thermoscene.energyplus.topology`) applied, without any model-specific "
                   f"configuration, to EnergyPlus example models. **{traced} of {total} conditioned zones traced; component-level "
                   f"precision {prec:.3f} and recall {rec:.3f} against the declared structure ({TP} of {TP + FN} declared "
                   f"zone-component pairs found, {FP} found but not declared); {exact_all} of {total} zones exact.**\n\n"
                   "*Traced zones*: conditioned zones for which a non-empty serving chain was recovered. *Precision / recall*: the "
                   "components the tracer finds by walking the node graph, compared with a reference built only from the objects "
                   "that declare the system structure (zone equipment list, air distribution unit and terminal, the air loop whose "
                   "supply path feeds the terminal with its supply branches, outdoor-air equipment, supply and return paths, and the "
                   "supply side of every plant or condenser loop whose demand branches hold a water-side component of the chain). "
                   "Micro-averaged over zones. Numbers in parentheses: how many traced zones contain that class.\n\n"
                   + "\n".join(rows) + detail + "\n")
    print(f"{traced}/{total} conditioned zones traced; precision {prec:.3f}, recall {rec:.3f} ({TP}/{TP + FN} declared, "
          f"{FP} undeclared), exact {exact_all}/{total}; wrote", out)


if __name__ == "__main__":
    main()
