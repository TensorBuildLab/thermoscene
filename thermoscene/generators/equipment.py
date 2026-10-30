"""Rooftop equipment from the traced topology.

* AHU / packaged unit: one casing section per air-loop supply component, in flow order (topological sort of the
  traced graph), plus a discharge section. A DX cooling coil makes it a packaged unit with a condenser section.
* Plant sources of the AHU water coils: air-cooled chillers and cooling towers on the roof, other sources in a plant
  room (not modelled); roof sources get pipes, pumps and valves, plant-room sources get pipes from a riser.
Dimensions follow ENGINEERING_RULE scaling from autosized flows/capacities; placement is ASSUMED_LAYOUT.
"""
from __future__ import annotations

import logging

import networkx as nx
import numpy as np
from shapely.ops import unary_union

from ..ontology import Medium, Surface
from ..scene.geometry import cyl, oriented_box, path, plan_polygon

log = logging.getLogger(__name__)
ROOF_SOURCES = {"Chiller:air": "roof", "Chiller:water": "plant_room", "Boiler": "plant_room", "CoolingTower": "roof"}


def roof_spec(gen, dist):
    r = gen.cfg["scene"]["roof"] or {}
    src = r.get("source", "none")
    if src == "none":
        return None
    if src == "model":
        roofs = [s for s in gen.scene.of(Surface) if s.space == "Outdoors" and s.surface_type == "Roof"]
        if not roofs:
            log.warning("roof.source=model but the scene has no roof surfaces; skipped")
            return None
        elev = max(p[2] for s in roofs for p in s.polygon)
        extent = unary_union([plan_polygon(s.polygon) for s in roofs])
    else:
        elev = float(r["elevation"]); x0, x1, y0, y1 = r["extent"]
        from shapely.geometry import box as sbox
        extent = sbox(x0, y0, x1, y1)
        gen.element("Roof_Deck", "RoofSurface", "Outdoors", [[(x0, y0, elev), (x1, y0, elev), (x1, y1, elev), (x0, y1, elev)]],
                    "sol_air", params=dict(horizontal_fraction=1.0, sky_fraction=1.0), size=f"{x1 - x0:.0f} x {y1 - y0:.0f} m",
                    layout_note="roof box from configuration")
    if dist:
        e0, a, w, _ = dist["service_edge"]
        anchor = e0 + 0.4 * w
    else:
        anchor = np.array(extent.centroid.coords[0]); a = np.array([1.0, 0.0]); w = np.array([0.0, 1.0])
    if isinstance(r.get("anchor"), (list, tuple)):
        anchor = np.array(r["anchor"], float)
    return dict(z=elev, extent=extent, anchor=np.asarray(anchor, float), a=np.asarray(a, float), w=np.asarray(w, float))


def _flow_order(gen, comps):
    keys = {c.id for c in comps}
    G = nx.DiGraph(); G.add_nodes_from(keys); G.add_edges_from(gen.graph.feeds_edges(keys))
    try:
        order = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        order = list(keys)
    return [gen.graph.comps[k] for k in order]


def _drop_containers(gen, comps):
    nodes = lambda c: {p.node for p in c.ports}
    return [c for c in comps if not (c.cls == "UnitarySystem" and any(o is not c and nodes(o) & nodes(c) for o in comps))]


def generate(gen, dist) -> dict:
    spec = roof_spec(gen, dist)
    if spec is None:
        return {}
    S, L, T = gen.S, gen.L, gen.T
    R, anchor, a, w = spec["z"], spec["anchor"], spec["a"], spec["w"]
    P = lambda along, across, z: (*(anchor + a * along + w * across), z)
    air = _drop_containers(gen, [c for c in gen.comps if gen.graph.has_role(c, "air_supply")])
    out = {"sections": []}
    if air:
        order = _flow_order(gen, air)[::-1]                          # downstream (fan) first, nearest the shaft
        q = gen.air_loop_design_flow()
        face = q / S["coil_face_velocity"]
        mc = S.get("min_casing_m", {"width": 1.2, "height": 0.9})
        W = round(max(mc["width"], np.sqrt(face * 1.2) + 0.3), 2)
        H = round(max(mc["height"], face / (W - 0.3) + 0.35), 2)
        z0 = R + L["roof_curb_m"]; zc = z0 + H / 2
        lens = S["ahu_section_length_m"]
        x = 0.0
        sections = [("Discharge", None, lens["Discharge"])] + [(c.name, c, lens.get(c.cls, 0.8)) for c in order]
        dx = next((c for c in air if c.cls == "DXCoolingCoil"), None)
        if dx is not None:
            sections.append(("Condenser", dx, lens["Condenser"]))
        air_out = lambda c: next((p.node for p in c.ports if p.direction == "out" and p.medium == Medium.AIR), None)
        last_out = air_out(order[0])                                  # discharge section carries the fan outlet air
        prev = None
        for name, c, ln in sections:
            sid_ = "AHU_" + "".join(ch if ch.isalnum() else "_" for ch in name)[:40]
            if name == "Condenser":
                cap = gen.size(c, "Capacity") or 10000.0
                flow = gen.T["condenser_air_flow_m3s_per_kw"] * cap / 1000
                e = gen.element(sid_, "CondenserCoil", "Outdoors", oriented_box(P(x + ln / 2, 0, zc), a, W, H, ln), "condenser_coil",
                                represents=[c.id], heat_rejection=[["Cooling Coil Total Cooling Rate", c.name],
                                                                   ["Cooling Coil Electricity Rate", c.name]],
                                condenser_airflow_m3s=flow, size=f"packaged condenser, {cap / 1000:.0f} kW")
                for k, f in enumerate((0.3, 0.7)):
                    gen.element(f"{sid_}_Fan_{k + 1}", "CondenserFan", "Outdoors",
                                cyl(P(x + f * ln, 0, z0 + H), P(x + f * ln, 0, z0 + H + 0.12), min(0.38, ln * 0.3), caps=True),
                                "condenser_fan", represents=[c.id], heat_rejection=e.attrs["heat_rejection"],
                                condenser_airflow_m3s=flow, size="axial condenser fan")
            else:
                node = air_out(c) if c else last_out
                e = gen.element(sid_, "EquipmentCasing", "Outdoors", oriented_box(P(x + ln / 2, 0, zc), a, W, H, ln), "casing",
                                stream=node, params=dict(coupling=T["casing_coupling"]["ahu"]), medium=Medium.AIR,
                                stagnation="air", represents=[c.id] if c else [], sim_component=c.id if c else "",
                                size=f"{q:.2f} m3/s air loop, {W} x {H} m section")
            if prev is not None:
                e.connected_to.append(prev.id)
            out["sections"].append((c, e, x, ln)); prev = e
            x += ln
        mixer = next((c for c in air if c.cls == "OutdoorAirMixer"), None)
        if mixer:
            mx = next(s for s in out["sections"] if s[0] is mixer)
            gen.element("AHU_OA_Louver", "Louver", "Outdoors",
                        oriented_box(P(mx[2] + mx[3] + 0.03, 0, zc), a, W - 0.4, H - 0.4, 0.06), "louver",
                        connects=[mx[1].id], size="outdoor-air intake")
            ret = mixer.port("return_air_stream", direction="in")
            if ret:
                rw, rh = gen.rect_duct(0.85 * q, S["duct_velocity"]["rooftop"], 0.5)
                across = W / 2 + 0.05 + rw / 2
                gen.element("Roof_Return_Duct", "Duct", "Outdoors",
                            oriented_box(P((mx[2] + mx[3] / 2) / 2, across, R + 0.25 + rh / 2), a, rw, rh, mx[2] + mx[3] / 2),
                            "casing", stream=ret.node, params=dict(coupling=T["casing_coupling"]["duct_outdoor"]),
                            medium=Medium.AIR, stagnation="air", connects=[mx[1].id], size=f"{rw:.2f} x {rh:.2f} m, insulated")
        out.update(W=W, H=H, z0=z0)
        _plant(gen, out, spec, P)
    return out


def _plant(gen, out, spec, P):
    S, L, T = gen.S, gen.L, gen.T
    R, W, z0 = spec["z"], out["W"], out["z0"]
    across_ch = -(W / 2 + L["roof_clearance_m"])
    placed = {}
    for c, e, x, ln in out["sections"]:
        if c is None or c.cls not in ("CoolingCoil", "HotWaterCoil", "HeatingCoil") or not c.port("water", direction="in"):
            continue
        w_in, w_out = c.port("water", direction="in"), c.port("water", direction="out")
        q_w = gen.size(c, "Water Flow Rate") or 1e-3
        od = gen.pipe(q_w, S["pipe_velocity"]["main"])
        tins = T["insulation"]["pipe_roof_m"]; r = od / 2 + tins
        coil_mid = x + ln / 2
        z_s, z_r = R + 0.6, R + 0.9
        for src in gen.graph.plant_sources(c):
            kind = src.cls
            if src.cls == "Chiller":
                cond_air = any(p.medium == Medium.AIR for p in src.ports if "condenser" in p.name)
                kind = "Chiller:air" if cond_air else "Chiller:water"
            where = gen.cfg.get("equipment_location", {}).get(kind, ROOF_SOURCES.get(kind, "plant_room"))
            s_out = next((p for p in src.ports if p.direction == "out" and p.medium == Medium.WATER), None)
            s_in = next((p for p in src.ports if p.direction == "in" and p.medium == Medium.WATER), None)
            if where == "roof" and src.id not in placed:
                cap = (gen.size(src, "Capacity") or 50000.0) / 1000
                Lc = S["chiller_length_m"]["base"] + S["chiller_length_m"]["per_kw"] * cap
                Wc, Hc = S["chiller_width_m"], S["chiller_height_m"]
                ac = across_ch - Wc / 2
                c0 = coil_mid - Lc / 2
                zb, zt = R + 0.15, R + 0.15 + Hc
                nm = "".join(ch if ch.isalnum() else "_" for ch in src.name)[:30]
                comp = gen.element(f"{nm}_Compressor", "EquipmentCasing", "Outdoors", oriented_box(P(c0 + 0.35, ac, (zb + zt) / 2), spec["a"], Wc, Hc, 0.7),
                                   "compressor", represents=[src.id], plr=["Chiller Part Load Ratio", src.name],
                                   size=f"{cap:.0f} kW {src.sim_type}")
                flow = gen.size(src, "Condenser Fluid Flow Rate") if kind == "Chiller:air" else None
                flow = flow or T["condenser_air_flow_m3s_per_kw"] * cap
                hr = [["Chiller Condenser Heat Transfer Rate", src.name]]
                coil_e = gen.element(f"{nm}_Condenser_Coil", "CondenserCoil", "Outdoors",
                                     oriented_box(P(c0 + 0.7 + (Lc - 0.7) / 2, ac, (zb + zt - 0.05) / 2), spec["a"], Wc, Hc - 0.05, Lc - 0.7),
                                     "condenser_coil", represents=[src.id], heat_rejection=hr, condenser_airflow_m3s=flow,
                                     size="V-coil condenser", connects=[comp.id])
                for k, f in enumerate((1 / 3, 2 / 3)):
                    fx = c0 + 0.7 + f * (Lc - 0.7)
                    gen.element(f"{nm}_Fan_{k + 1}", "CondenserFan", "Outdoors", cyl(P(fx, ac, zt - 0.05), P(fx, ac, zt + 0.1), 0.38, caps=True),
                                "condenser_fan", represents=[src.id], heat_rejection=hr, condenser_airflow_m3s=flow,
                                size="0.76 m axial fan", connects=[coil_e.id])
                placed[src.id] = (ac, Wc)
            if where == "roof":
                ac, Wc = placed[src.id]
                face = ac + Wc / 2
                y_coil = -W / 2
                sup = gen.element(f"{src.name[:20]}_{c.name[:20]}_Supply_Pipe".replace(" ", "_"), "Pipe", "Outdoors",
                                  path([P(coil_mid - 0.2, face, z_s), P(coil_mid - 0.2, y_coil, z_s)], r), "insulated_pipe",
                                  stream=s_out.node, params=dict(od=od, thickness=tins), medium=Medium.WATER, stagnation="water",
                                  represents=[src.id, c.id], size=f"{od * 1000:.0f} mm OD + {tins * 1000:.0f} mm, Al jacket")
                ret_q = path([P(coil_mid + 0.2, y_coil, z_r), P(coil_mid + 0.2, y_coil - 0.35, z_r)], r) + \
                    path([P(coil_mid + 0.2, y_coil - 0.6, z_r), P(coil_mid + 0.2, face, z_r)], r)
                ret = gen.element(f"{src.name[:20]}_{c.name[:20]}_Return_Pipe".replace(" ", "_"), "Pipe", "Outdoors", ret_q,
                                  "insulated_pipe", stream=s_in.node, params=dict(od=od, thickness=tins), medium=Medium.WATER,
                                  stagnation="water", represents=[src.id, c.id], size=f"{od * 1000:.0f} mm OD + {tins * 1000:.0f} mm")
                gen.element(f"{c.name[:24]}_Control_Valve".replace(" ", "_"), "Valve", "Outdoors",
                            cyl(P(coil_mid + 0.2, y_coil - 0.35, z_r), P(coil_mid + 0.2, y_coil - 0.6, z_r), od / 2 + 0.02, caps=True),
                            "bare_pipe", stream=w_out.node, medium=Medium.WATER, stagnation="valve", condensation_check=True,
                            size="control valve, uninsulated", connects=[ret.id])
                loop = next((s for r_, s in c.attrs.get("memberships", []) if r_ == "plant_demand"), "")
                for k, pump in enumerate(gen.graph.loop_members(loop, "plant_supply", "Pump")[:1]):
                    yp = (face + y_coil) / 2
                    gen.element(f"{pump.name[:30]}_Motor".replace(" ", "_"), "Motor", "Outdoors",
                                cyl(P(coil_mid - 0.2, yp, z_s + r), P(coil_mid - 0.2, yp, z_s + r + 0.45), 0.12, caps=True),
                                "motor", represents=[pump.id], power=["Pump Electricity Rate", pump.name],
                                size="inline pump motor", connects=[sup.id])
            else:
                riser = (W / 2 + 0.6)
                for tag, port, zz, dx in (("Supply", w_in, z_s, -0.2), ("Return", w_out, z_r, 0.2)):
                    gen.element(f"{c.name[:24]}_{tag}_From_Riser".replace(" ", "_"), "Pipe", "Outdoors",
                                path([P(dx, riser, R), P(dx, riser, zz), P(coil_mid + dx, riser, zz), P(coil_mid + dx, W / 2, zz)], r),
                                "insulated_pipe", stream=port.node, params=dict(od=od, thickness=tins), medium=Medium.WATER,
                                stagnation="water", represents=[c.id, src.id],
                                size=f"{od * 1000:.0f} mm from {where} ({src.sim_type})")
