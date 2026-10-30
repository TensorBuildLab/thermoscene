"""Shared machinery for generators: sizing rules, design flows, material rules, element factory."""
from __future__ import annotations

import logging

import numpy as np

from ..ontology import Element, Medium, Provenance

log = logging.getLogger(__name__)


class Gen:
    def __init__(self, scene, graph, results, cfg, model, ids, chain):
        self.scene, self.graph, self.r, self.cfg, self.model, self.ids, self.chain = scene, graph, results, cfg, model, ids, chain
        self.sizes = results.component_sizes()
        self.S = cfg["sizing"]; self.L = cfg["layout"]; self.T = cfg["thermal"]
        self.comps = [c for c, _ in chain["components"]] if chain else []
        self.zone = scene.get(ids["zone"])

    # ---------------------------------------------------------------- sizing rules (ENGINEERING_RULE)
    def round_duct(self, q, v):
        d = np.sqrt(4 * q / (np.pi * v))
        sizes = self.S["round_ducts_m"]
        if d > sizes[-1]:
            log.warning("round duct for %.2f m3/s at %.1f m/s needs %.0f mm; using the largest size, %.0f mm (%.1f m/s)",
                        q, v, d * 1000, sizes[-1] * 1000, 4 * q / (np.pi * sizes[-1] ** 2))
        return next((x for x in sizes if x >= d), sizes[-1])

    def rect_duct(self, q, v, max_h):
        a = q / v
        m = self.S.get("min_rect_duct_m", 0.15)
        h = max(m, min(max_h, np.sqrt(a / 2)))
        return round(max(m, a / h), 2), round(h, 2)

    def pipe(self, q, v):
        d = np.sqrt(4 * q / (np.pi * v))
        ids = self.S["steel_pipe_id_m"]
        i = next((k for k, x in enumerate(ids) if x >= d), len(ids) - 1)
        return self.S["steel_pipe_od_m"][i]

    # ---------------------------------------------------------------- design values from EnergyPlus autosizing
    def size(self, comp, *keywords):
        for desc, v in self.sizes.get(comp.name.upper(), {}).items():
            if all(k.lower() in desc.lower() for k in keywords):
                return v
        return None

    def node_max_flow(self, node):
        try:
            return float(np.nanmax(self.r.series("System Node Mass Flow Rate", node)))
        except KeyError:
            return 0.0

    def zone_design_flow(self) -> float:
        """Design supply air volume flow for ONE zone instance (m3/s)."""
        mult = self.zone.multiplier
        for c in self.comps:
            if c.role in ("terminal", "zone_equipment"):
                v = self.size(c, "Maximum Air Flow Rate") or self.size(c, "Maximum Supply Air Flow Rate")
                if v:
                    return v / mult
        m = sum(self.node_max_flow(n) for n in self.chain.get("inlet_nodes", []))
        return m / self.S["air_density"] / mult if m else 0.1

    def air_loop_design_flow(self) -> float:
        for c in self.comps:
            if c.cls == "Fan" and self.graph.has_role(c, "air_supply"):
                v = self.size(c, "Maximum Flow Rate") or self.size(c, "Design Maximum Air Flow Rate")
                if v:
                    return v
        return self.zone_design_flow() * self.zone.multiplier

    # ---------------------------------------------------------------- elements
    def location(self, space_id):
        cls = self.scene.get(space_id).cls
        return {"Zone": "zone", "Plenum": "plenum", "Outdoors": "roof"}[cls]

    def material_for(self, cls, space_id, override=None):
        if override:
            return override
        rules = self.cfg["material_rules"][self.location(space_id)]
        if cls not in rules:
            raise KeyError(f"no material rule for {cls} in {self.location(space_id)}")
        return rules[cls]

    def element(self, id_, cls, space, quads, model, *, stream=None, params=None, material=None, medium=Medium.NONE,
                represents=(), connects=(), size="", layout_note="", stagnation=None, **attrs) -> Element:
        e = Element(id=f"{space}/{id_}", cls=cls, name=id_, space=space, quads=quads, thermal_model=model,
                    thermal_params=params or {}, material=self.material_for(cls, space, material), medium=medium,
                    stream_node=stream, represents=list(represents), connected_to=list(connects))
        e.attrs.update(attrs, size=size)
        if stagnation:
            e.attrs["stagnation"] = stagnation
        e.prov("geometry", Provenance.ASSUMED_LAYOUT, layout_note or "rule-based layout")
        e.prov("size", Provenance.ENGINEERING_RULE, size)
        e.prov("material", Provenance.ASSUMED_LAYOUT, f"material rule: {e.material}")
        return self.scene.add(e)

    def find(self, cls=None, role=None, sim_prefix=None):
        return [c for c in self.comps if (cls is None or c.cls == cls) and (role is None or self.graph.has_role(c, role))
                and (sim_prefix is None or c.sim_type.startswith(sim_prefix))]
