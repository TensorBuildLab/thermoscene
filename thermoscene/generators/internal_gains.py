"""Occupants, luminaires and workstations generated from the energy model's internal gains.

What EnergyPlus simulates for these loads is used as it is: the occupant count, the people's total heat gain (which
gives the metabolic rate per person), the lighting power and the plug-load power of the zone, at every time step.
What it does not contain is where they are and what their surfaces look like to a camera; those are generated here
under explicit rules (defaults.yaml, `internal_gains`):

* workstations (desk, monitor, seated occupant) at well-separated points of the zone floor, as many as the zone's
  peak occupant count (capped); at each time step the first round(count) occupants are present, the others absent
  (`present` series), so the scene follows the occupancy schedule;
* luminaires in the ceiling, as many as the peak lighting power divided by a fixture wattage, away from the
  diffusers and grilles;
* surface temperatures: occupant clothing from the ISO 7730 clothing-surface-temperature equation with the simulated
  air and mean radiant temperatures and metabolic rate, and clothing insulation from the ASHRAE 55 dynamic clothing
  model (outdoor air temperature at 06:00); luminaire lenses and
  monitors at the ceiling or air temperature plus a rise proportional to the simulated power fraction.
Exposed skin takes the bare-skin limit of the same ISO 7730 equation.
"""
from __future__ import annotations

import logging
import math

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from ..ontology import Provenance, Surface
from ..scene.geometry import oriented_box, plan_polygon
from .air_devices import interior_points

log = logging.getLogger(__name__)

OUTPUTS = ["Zone People Occupant Count", "Zone People Total Heating Rate", "Zone Lights Electricity Rate",
           "Zone Electric Equipment Electricity Rate"]


def output_objects() -> list[str]:
    return [f"Output:Variable, *, {v}, Timestep;" for v in OUTPUTS]


def farthest_points(pts, n, min_dist, rng):
    """Up to n points of `pts`, each as far as possible from those already chosen and at least min_dist apart."""
    if not len(pts) or n <= 0:
        return np.zeros((0, 2))
    chosen = [pts[rng.integers(len(pts))]]
    d = np.linalg.norm(pts - chosen[0], axis=1)
    while len(chosen) < n:
        k = int(np.argmax(d))
        if d[k] < min_dist:
            break
        chosen.append(pts[k]); d = np.minimum(d, np.linalg.norm(pts - pts[k], axis=1))
    return np.array(chosen)


def principal_axis(poly):
    r = poly.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e = [c[1] - c[0], c[2] - c[1]]
    a = max(e, key=np.linalg.norm)
    return a / (np.linalg.norm(a) or 1.0)


def generate(gen, devices: dict) -> dict:
    cfg = gen.cfg.get("internal_gains", {})
    out = {"workstations": 0, "luminaires": 0}
    if not cfg.get("enabled", False):
        return out
    z, key, r = gen.ids["zone"], gen.zone.sim_zone, gen.r
    rng = np.random.default_rng(int(cfg.get("seed", 0)))
    sig = lambda var: np.nan_to_num(r.series(var, key, default=0.0)) if r.has(var, key) else np.zeros(r.n)
    occ, lights, plug = sig(OUTPUTS[0]), sig(OUTPUTS[2]), sig(OUTPUTS[3])
    floors = [s for s in gen.scene.of(Surface) if s.space == z and s.cls == "Surface" and s.surface_type == "Floor"]
    ceilings = [s for s in gen.scene.of(Surface) if s.space == z and s.cls == "Surface" and s.surface_type == "Ceiling"]
    ws = cfg["workstation"]
    axis = None
    # ------------------------------------------------------------------ luminaires
    lum = cfg["luminaire"]
    if ceilings and lights.max() > 0:
        polys = [plan_polygon(s.polygon) for s in ceilings]
        plan = unary_union(polys)
        axis = principal_axis(plan)
        z_c = float(np.mean([p[2] for s in ceilings for p in s.polygon]))
        p_max = float(lights.max())
        n = int(min(cfg["max_luminaires"], max(1, round(p_max / lum["fixture_w"])),
                    max(1, math.floor(plan.area / lum["min_area_per_fixture_m2"]))))
        pts = interior_points(plan, lum["inset_m"])
        busy = [np.asarray(e.attrs.get("jet_center", e.quads[0][0]))[:2] for e in devices.get("diffusers", []) + devices.get("grilles", [])]
        if busy and len(pts):
            dmin = np.min(np.linalg.norm(pts[:, None, :] - np.asarray(busy)[None], axis=2), axis=1)
            pts = pts[dmin >= lum["clearance_from_air_devices_m"]] if np.any(dmin >= lum["clearance_from_air_devices_m"]) else pts
        centers = farthest_points(pts, n, lum["min_spacing_m"], rng)
        w_, l_ = lum["size_m"]
        for i, (x, y) in enumerate(centers):
            ceil = ceilings[next((k for k, p in enumerate(polys) if p.contains(Point(x, y))), 0)]
            gen.element(f"Luminaire_{i + 1}", "Luminaire", z,
                        oriented_box((x, y, z_c - lum["depth_m"] / 2), axis, w_, lum["depth_m"], l_), "luminaire",
                        material="luminaire_lens", ceiling_surface=ceil.id, power=("Zone Lights Electricity Rate", key),
                        design_power_w=p_max, size=f"{w_} x {l_} m, {p_max / len(centers):.0f} W at peak",
                        layout_note="farthest-point layout in the ceiling, clear of air devices")
        out["luminaires"] = len(centers)
        if len(centers) < n:
            log.info("internal gains: %d of %d luminaires fit the ceiling", len(centers), n)
    # ------------------------------------------------------------------ workstations and occupants
    n_people = int(math.ceil(float(occ.max()) - 1e-9)) if occ.max() > 0 else 0
    if floors and n_people:
        fpolys = [plan_polygon(s.polygon) for s in floors]
        fplan = unary_union(fpolys)
        axis = principal_axis(fplan) if axis is None else axis
        perp = np.array([-axis[1], axis[0]])
        z_f = float(np.mean([p[2] for s in floors for p in s.polygon]))
        n = min(n_people, cfg["max_occupants"])
        pts = interior_points(fplan, ws["inset_m"])
        spots = farthest_points(pts, n, ws["spacing_m"], rng)
        if len(spots) < n:
            log.info("internal gains: %d of %d workstations fit the floor", len(spots), n)
        p_plug = float(plug.max())
        cl = cfg["occupant"]
        w3 = np.r_[perp, 0.0]
        for i, (x, y) in enumerate(spots):
            c = np.array([x, y, z_f])
            dl, dw, dh = ws["desk_m"]
            gen.element(f"Desk_{i + 1}", "Furniture", z, oriented_box(c + [0, 0, dh / 2], axis, dw, dh, dl), "ambient",
                        material="desk", size=f"{dl} x {dw} m desk", layout_note="farthest-point workstation layout")
            if p_plug > 0:
                ml, md, mh = ws["monitor_m"]
                gen.element(f"Monitor_{i + 1}", "PlugLoad", z,
                            oriented_box(c - w3 * (dw / 2 - md) + [0, 0, dh + 0.04 + mh / 2], axis, md, mh, ml), "plug_load",
                            material="monitor", power=("Zone Electric Equipment Electricity Rate", key), design_power_w=p_plug,
                            size=f"{ml} x {mh} m screen", layout_note="on the workstation desk")
            tl, td, th = cl["torso_m"]
            seat = c + w3 * (dw / 2 + cl["chair_offset_m"])
            torso = oriented_box(seat + [0, 0, cl["seat_m"] + th / 2], axis, td, th, tl)
            legs = oriented_box((c + seat) / 2 + [0, 0, cl["seat_m"] / 2], axis, float(np.linalg.norm(seat - c)) + td / 2,
                                cl["seat_m"], tl * 0.9)
            hl, hd, hh = cl["head_m"]
            head = oriented_box(seat + [0, 0, cl["seat_m"] + th + 0.02 + hh / 2], axis, hd, hh, hl)
            present = occupant_presence(occ, i, n)
            for part, quads, model, mat in (("Body", torso + legs, "occupant_clothing", "clothing"),
                                            ("Head", head, "occupant_skin", "skin")):
                e = gen.element(f"Occupant_{i + 1}_{part}", "Occupant", z, quads, model, material=mat,
                                people=("Zone People Occupant Count", key), people_heat=("Zone People Total Heating Rate", key),
                                size="seated person", layout_note="seated at a generated workstation")
                gen.scene.set_series(e.id, "present", present, Provenance.SIMULATED,
                                     f"present while the simulated occupant count exceeds {i} (person {i + 1} of {n})")
        out["workstations"] = len(spots)
    return out


def occupant_presence(count, i, n_bodies):
    """Person i (0-based) is present while round(count) > i; above n_bodies people every body is present."""
    return np.round(np.minimum(np.nan_to_num(count), n_bodies)) > i


# ---------------------------------------------------------------------------------------------- thermal comfort
def ashrae55_clothing(t_out_6am):
    """Clothing insulation (clo) from the outdoor air temperature at 06:00 (ASHRAE 55 dynamic clothing model,
    Schiavon and Lee 2013)."""
    t = np.asarray(t_out_6am, float)
    return np.where(t < -5, 1.0, np.where(t < 5, 0.818 - 0.0364 * t, np.where(t < 26, 10 ** (-0.1635 - 0.0066 * t), 0.46)))


def clothing_temperature(ta, tr, met_w_m2, clo, v=0.1, iterations=200):
    """ISO 7730 clothing surface temperature (deg C), solved by damped fixed-point iteration (vectorised)."""
    ta, tr, M, clo = (np.asarray(x, float) for x in (ta, tr, met_w_m2, clo))
    icl = 0.155 * clo
    fcl = np.where(icl <= 0.078, 1.0 + 1.290 * icl, 1.05 + 0.645 * icl)
    hcf = 12.1 * np.sqrt(v)
    tcl = ta + (35.5 - ta) / (3.5 * icl + 0.1)
    for _ in range(iterations):
        hc = np.maximum(hcf, 2.38 * np.abs(tcl - ta) ** 0.25)
        new = 35.7 - 0.028 * M - icl * (3.96e-8 * fcl * ((tcl + 273.0) ** 4 - (tr + 273.0) ** 4) + fcl * hc * (tcl - ta))
        tcl = 0.5 * tcl + 0.5 * new
    return tcl


def skin_temperature(met_w_m2):
    """Exposed skin (deg C): the bare-skin limit (I_cl = 0) of the ISO 7730 clothing-surface equation,
    35.7 - 0.028 (M - W), for metabolic rate M (W/m2) and no external work."""
    return 35.7 - 0.028 * np.asarray(met_w_m2, float)
