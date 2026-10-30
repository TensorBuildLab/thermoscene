"""Supply diffusers and return grilles in the zone ceiling.

Rules (ENGINEERING_RULE): count = max(ceil(Q / q_max), ceil(A / A_max), 1); diffusers at k-means centres of the
inset ceiling polygon; grilles at the interior points farthest from all diffusers.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import shapely
from scipy.cluster.vq import kmeans2
from shapely.geometry import Point
from shapely.ops import unary_union

from ..energyplus.model import newell_normal
from ..ontology import Medium, Surface
from ..scene.geometry import box, plan_polygon

log = logging.getLogger(__name__)


def interior_points(poly, inset, step=0.1):
    p = poly.buffer(-inset)
    if p.is_empty or p.area < 0.05:
        p = poly.buffer(-min(inset, 0.2))
    xmin, ymin, xmax, ymax = p.bounds
    gx, gy = np.meshgrid(np.arange(xmin, xmax + 1e-9, step), np.arange(ymin, ymax + 1e-9, step), indexing="ij")
    gx, gy = gx.ravel(), gy.ravel()
    inside = shapely.contains_xy(p, gx, gy)                  # vectorised point-in-polygon, same order as a nested loop
    return np.c_[gx[inside], gy[inside]] if inside.any() else np.array([p.representative_point().coords[0]])


def generate(gen) -> dict:
    z = gen.ids["zone"]
    ceilings = [s for s in gen.scene.of(Surface) if s.space == z and s.cls == "Surface" and s.surface_type == "Ceiling"]
    if not ceilings:              # top-floor zone without plenum: devices hang from the (near-horizontal) roof
        cos_max = np.cos(np.radians(gen.L["ceiling_from_roof_max_tilt_deg"]))
        ceilings = [s for s in gen.scene.of(Surface) if s.space == z and s.cls == "Surface" and s.surface_type == "Roof"
                    and abs(newell_normal(s.polygon)[2]) >= cos_max]
        if ceilings:
            log.info("air devices: zone has no ceiling; mounted on its roof surfaces")
    inlets = gen.chain.get("inlet_nodes", []) if gen.chain else []
    if not ceilings or not inlets:
        log.info("air devices: no ceiling or no air inlet; skipped")
        return {}
    polys = [plan_polygon(s.polygon) for s in ceilings]
    ceiling_plan = unary_union(polys)
    z_c = float(np.mean([p[2] for s in ceilings for p in s.polygon]))
    S = gen.S["diffuser"]
    Q = gen.zone_design_flow()
    q_node = gen.node_design_flows() if len(inlets) > 1 else {}       # several terminals: each node its own flow
    n = max(math.ceil(Q / S["max_flow_m3s"]), math.ceil(ceiling_plan.area / S["max_area_m2"]), len(inlets), 1)
    pts = interior_points(ceiling_plan, S["inset_m"])
    centers = np.array([pts.mean(0)]) if n == 1 else kmeans2(pts, n, seed=0, minit="++")[0]
    if n == 1 and not ceiling_plan.contains(Point(*centers[0])):
        centers = pts[[np.argmin(np.linalg.norm(pts - centers[0], axis=1))]]
    rho, mult = gen.S["air_density"], gen.zone.multiplier
    out = {"diffusers": [], "grilles": [], "ceiling_z": z_c, "plan": ceiling_plan}
    half = S["size_m"] / 2
    for i, (x, y) in enumerate(centers):
        ceil = ceilings[next((k for k, p in enumerate(polys) if p.contains(Point(x, y))), 0)]
        node = inlets[i % len(inlets)]
        count = sum(1 for j in range(n) if inlets[j % len(inlets)] == node)
        share = 1.0 / (mult * count)
        q_d = q_node[node] / count if q_node.get(node) else Q / n
        flow_kg_s = q_d * rho if q_node.get(node) else Q * rho / n
        e = gen.element(f"Supply_Diffuser_{i + 1}", "Diffuser", z, box(x - half, x + half, y - half, y + half, z_c - 0.03, z_c),
                        "diffuser_face", stream=node, medium=Medium.AIR, flow_scale=share,
                        design_flow_kg_s=flow_kg_s, ceiling_surface=ceil.id, jet_center=(float(x), float(y), z_c), jet_half=half,
                        size=f"{S['size_m']} m square, {q_d:.3f} m3/s", layout_note="k-means centre of the inset ceiling")
        out["diffusers"].append(e)
    ret_nodes = []
    for _, f in gen.model.objs("ZoneHVAC:EquipmentConnections").items():
        if f["zone_name"].upper() == gen.zone.sim_zone.upper():
            ret_nodes = gen.graph.expand(f.get("zone_return_air_node_or_nodelist_name"))
    if ret_nodes:
        m = max(1, n // 2)
        chosen = []
        d = np.min(np.linalg.norm(pts[:, None, :] - centers[None], axis=2), axis=1)   # distance to the nearest device
        for k in range(m):
            chosen.append(pts[np.argmax(d)])
            d = np.minimum(d, np.linalg.norm(pts - chosen[-1], axis=1))
        g = gen.S["return_grille"]["size_m"] / 2
        for k, (x, y) in enumerate(chosen):
            ceil = ceilings[next((i for i, p in enumerate(polys) if p.contains(Point(x, y))), 0)]
            out["grilles"].append(gen.element(
                f"Return_Grille_{k + 1}", "ReturnGrille", z, box(x - g, x + g, y - g, y + g, z_c - 0.004, z_c), "return_grille",
                stream=ret_nodes[0], medium=Medium.AIR, ceiling_surface=ceil.id, size=f"{2 * g} m square",
                layout_note="interior point farthest from the diffusers"))
    return out
