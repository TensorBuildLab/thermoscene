"""Air and water distribution in the plenum above the zone, driven by the traced topology.

Layout rules (ASSUMED_LAYOUT, parameters in config `layout`):
  * service edge = longest edge of the zone footprint that is not an exterior wall; mains run along it
  * terminal box between the main and the diffuser cluster, shifted sideways so it does not sit on a diffuser
  * discharge ducts: orthogonal route box -> above each diffuser -> vertical drop
  * hot-water reheat piping only if the traced reheat coil is a water coil; orthogonal route from the service edge
Sizes (ENGINEERING_RULE) come from autosized flows and design velocities.
"""
from __future__ import annotations

import logging

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from ..ontology import Medium, Surface
from ..scene.geometry import cyl, oriented_box, path

log = logging.getLogger(__name__)


def _service_edge(gen, footprint):
    walls = [s for s in gen.scene.of(Surface) if s.space == gen.ids["zone"] and s.cls == "Surface" and s.surface_type == "Wall"]
    ext = unary_union([LineString(np.asarray(s.polygon)[:, :2]) for s in walls if s.boundary.lower() == "outdoors"]
                      or [LineString([(1e9, 1e9), (1e9 + 1, 1e9)])])
    ring = np.asarray(footprint.exterior.coords)
    edges = []
    for p, q in zip(ring[:-1], ring[1:]):
        mid = (p + q) / 2
        edges.append((np.linalg.norm(q - p), ext.distance(Point(mid)) > 0.05, p, q))
    interior = [e for e in edges if e[1]]
    L, _, p, q = max(interior or edges, key=lambda e: e[0])
    a = (q - p) / L
    w = np.array([-a[1], a[0]])
    if not footprint.contains(Point(*(p + a * L / 2 + 0.1 * w))):
        w = -w
    return np.asarray(p, float), a, w, L, bool(interior)


def generate(gen, devices, faults) -> dict:
    if not gen.ids.get("plenum") or not devices.get("diffusers"):
        log.info("distribution: no plenum or no diffusers; skipped")
        return {}
    terminal = next((c for c in gen.comps if c.role == "terminal"), None)
    if terminal is None:
        log.info("distribution: zone has no air terminal unit; skipped")
        return {}
    inlets = gen.chain["inlet_nodes"]
    t_in = next(p for p in terminal.ports if p.direction == "in" and p.medium == Medium.AIR and "secondary" not in p.name)
    t_damper = terminal.port("damper", direction="out") or t_in
    reheat = next((c for c in gen.comps if c.cls in ("HeatingCoil", "HotWaterCoil")
                   and any(p.direction == "out" and p.node in inlets for p in c.ports)), None)
    zone_node = inlets[0]
    mult = gen.zone.multiplier
    pl = gen.scene.get(gen.ids["plenum"])
    pid = pl.id
    z0 = devices["ceiling_z"] + 0.01
    z1 = pl.z_ceiling
    depth = z1 - z0
    footprint = devices["plan"]
    e0, a, w, Le, has_interior = _service_edge(gen, footprint)
    P = lambda along, across, z: (*(e0 + a * along + w * across), z)
    S, L, T = gen.S, gen.L, gen.T
    out = {"service_edge": (e0, a, w, Le), "elements": []}
    note = "service edge = longest non-exterior zone edge" if has_interior else "no interior edge: longest edge used"

    # ---------------- main duct
    q_main = S["main_duct_flow_fraction"] * gen.air_loop_design_flow()
    mw, mh = gen.rect_duct(q_main, S["duct_velocity"]["main"], 0.4 * depth)
    zd = float(np.clip(z0 + L["plenum_duct_elevation_frac"] * depth, z0 + mh / 2 + 0.05, z1 - mh / 2 - 0.05))
    c_off = L["main_wall_clearance_m"] + mw / 2
    main = gen.element("Supply_Main_Duct", "Duct", pid, oriented_box(P(Le / 2, c_off, zd), a, mw, mh, Le - 0.1),
                       "insulated_flat", stream=t_in.node, params=dict(thickness=T["insulation"]["duct_wrap_m"]),
                       medium=Medium.AIR, stagnation="air", size=f"{mw:.2f} x {mh:.2f} m, {q_main:.2f} m3/s",
                       layout_note=note)
    # ---------------- terminal box (+ reheat section)
    q_t = gen.zone_design_flow()
    D = gen.round_duct(q_t, S["duct_velocity"]["branch"])
    Lb, Wb, Hb = max(0.5, 2.5 * D), max(0.4, D + 0.25), min(max(0.25, D + 0.1), depth - 0.15)
    dc = np.mean([e.attrs["jet_center"][:2] for e in devices["diffusers"]], axis=0)
    t_proj = float(np.clip((dc - e0) @ a, Wb, Le - Wb))
    shift = Wb / 2 + 0.35
    along = t_proj + shift if t_proj + shift + Wb / 2 < Le - 0.05 else t_proj - shift
    b0 = c_off + mw / 2 + L["terminal_offset_from_main_m"]
    Lr = 0.2 if reheat else 0.0
    box = gen.element("Terminal_Box", "TerminalBox", pid, oriented_box(P(along, b0 + Lb / 2, zd), w, Wb, Hb, Lb),
                      "insulated_flat", stream=t_damper.node, params=dict(thickness=T["insulation"]["box_lining_m"]),
                      medium=Medium.AIR, stagnation="air", represents=[terminal.id],
                      size=f"{Lb:.2f} x {Wb:.2f} x {Hb:.2f} m, inlet {D * 1000:.0f} mm", layout_note="between main and diffusers")
    branch = gen.element("Supply_Branch_Duct", "Duct", pid, cyl(P(along, c_off + mw / 2, zd), P(along, b0, zd), D / 2),
                         "insulated_flat", stream=t_in.node, params=dict(thickness=T["insulation"]["duct_wrap_m"]),
                         medium=Medium.AIR, stagnation="air", size=f"{D * 1000:.0f} mm round", connects=[box.id])
    main.connected_to.append(branch.id)
    last = box
    if reheat:
        rh = gen.element("Reheat_Section", "CoilSection", pid, oriented_box(P(along, b0 + Lb + Lr / 2, zd), w, Wb, Hb, Lr),
                         "insulated_flat", stream=zone_node, params=dict(thickness=0.01), medium=Medium.AIR, stagnation="air",
                         represents=[reheat.id], size=f"{reheat.sim_type}", layout_note="downstream end of the box")
        box.connected_to.append(rh.id); last = rh
    outlet = b0 + Lb + Lr

    # ---------------- discharge ducts to each diffuser (+ optional insulation-gap fault on the first drop)
    gap = next((f for f in faults if f["type"] == "duct_insulation_gap"), None)
    nd = len(devices["diffusers"])
    Dd = gen.round_duct(q_t / nd, S["duct_velocity"]["branch"])          # each run carries its diffuser's share
    for i, dfu in enumerate(devices["diffusers"]):
        x, y, zc = dfu.attrs["jet_center"]
        rel = np.array([x, y]) - e0
        da, dw = float(rel @ a), float(rel @ w)
        horiz = [P(along, outlet, zd), P(along, dw, zd), P(da, dw, zd)]
        top, bot = P(da, dw, zd), P(da, dw, zc + 0.01)
        drop_quads = path(horiz, Dd / 2)
        gap_quads = None
        if gap and i == 0 and gap.get("on", "discharge") == "discharge":
            g0 = zd - Dd / 2 - gap.get("position", 0.4) * (zd - Dd / 2 - zc)
            g1 = g0 - gap.get("length_m", 0.18)
            drop_quads += cyl((top[0], top[1], zd), (top[0], top[1], g0), Dd / 2) + cyl((top[0], top[1], g1), bot, Dd / 2)
            gap_quads = cyl((top[0], top[1], g0), (top[0], top[1], g1), Dd / 2)
        else:
            drop_quads += cyl((top[0], top[1], zd), bot, Dd / 2)
        dis = gen.element(f"Discharge_Duct_{i + 1}", "Duct", pid, drop_quads, "insulated_flat", stream=zone_node,
                          params=dict(thickness=T["insulation"]["duct_wrap_m"]), medium=Medium.AIR, stagnation="air",
                          flow_scale=1.0 / mult, size=f"{Dd * 1000:.0f} mm round", connects=[dfu.id],
                          layout_note="orthogonal route box -> diffuser")
        last.connected_to.append(dis.id)
        if gap_quads:
            gen.element("Duct_Insulation_Gap", "InsulationDefect", pid, gap_quads, "bare_duct", stream=zone_node,
                        medium=Medium.AIR, stagnation="air", flow_scale=1.0 / mult, connects=[dfu.id],
                        size=f"{gap.get('length_m', 0.18)} m unwrapped", fault="duct_insulation_gap",
                        layout_note="injected fault: missing duct wrap")

    # ---------------- hot-water reheat piping
    if reheat and reheat.port("water", direction="in"):
        w_in, w_out = reheat.port("water", direction="in"), reheat.port("water", direction="out")
        q_w = (gen.size(reheat, "Maximum Water Flow Rate") or 2e-5 * mult) / mult
        od = gen.pipe(q_w, S["pipe_velocity"]["branch"])
        tins = T["insulation"]["pipe_plenum_m"]; r = od / 2 + tins
        zp = z1 - L["plenum_pipe_clearance_m"]
        wc = b0 + Lb + Lr / 2
        pa_s, pa_r = along + Wb / 2 + 0.12, along + Wb / 2 + 0.24
        side = along + Wb / 2
        zv = zd + 0.1
        sup = gen.element("HW_Supply_Pipe", "Pipe", pid,
                          path([P(pa_s, 0.08, zp), P(pa_s, wc - 0.04, zp), P(pa_s, wc - 0.04, zv + 0.12)], r) +
                          path([P(pa_s, wc - 0.04, zv), P(pa_s, wc - 0.04, zd), P(side, wc - 0.04, zd)], r),
                          "insulated_pipe", stream=w_in.node, params=dict(od=od, thickness=tins), medium=Medium.WATER,
                          stagnation="water", flow_scale=1.0 / mult, represents=[reheat.id],
                          size=f"{od * 1000:.0f} mm OD + {tins * 1000:.0f} mm insulation")
        valve = gen.element("HW_Control_Valve", "Valve", pid,
                            cyl(P(pa_s, wc - 0.04, zv + 0.12), P(pa_s, wc - 0.04, zv), od / 2 + 0.015, caps=True),
                            "bare_pipe", stream=w_in.node, medium=Medium.WATER, stagnation="valve", flow_scale=1.0 / mult,
                            size="2-way control valve, uninsulated", connects=[sup.id])
        ret = gen.element("HW_Return_Pipe", "Pipe", pid,
                          path([P(side, wc + 0.04, zd - 0.05), P(pa_r, wc + 0.04, zd - 0.05), P(pa_r, wc + 0.04, zp - 0.06),
                                P(pa_r, 0.08, zp - 0.06)], r),
                          "insulated_pipe", stream=w_out.node, params=dict(od=od, thickness=tins), medium=Medium.WATER,
                          stagnation="water", flow_scale=1.0 / mult, represents=[reheat.id],
                          size=f"{od * 1000:.0f} mm OD + {tins * 1000:.0f} mm insulation")
        hdr = gen.element("Reheat_Coil_Header", "Pipe", pid, cyl(P(side, wc - 0.08, zd - 0.02), P(side, wc + 0.08, zd - 0.02), 0.015, caps=True),
                          "bare_pipe", stream=w_in.node, medium=Medium.WATER, stagnation="valve", flow_scale=1.0 / mult,
                          material="copper", size="exposed copper return bends", connects=[ret.id])
        sup.connected_to.append(valve.id); valve.connected_to.append(hdr.id)

    # ---------------- return opening (return plenum only)
    return_plenums = {f["zone_name"].upper() for f in gen.model.objs("AirLoopHVAC:ReturnPlenum").values()}
    if pl.sim_zone and pl.sim_zone.upper() in return_plenums:
        ra = Le - 0.5 if along < Le / 2 else 0.5
        gen.element("Return_Opening", "Duct", pid, oriented_box(P(ra, 0.2, zd), w, 0.5, min(0.4, depth - 0.2), 0.4),
                    "ambient", stream=f"space:{pid}", material="galv_bare", size="0.5 m return stub to the shaft")
    return out
