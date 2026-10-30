"""
Generic HVAC topology from an EnergyPlus model.

Every object that names inlet/outlet nodes becomes a SimComponent; components sharing a node are
connected. Implicit links come from AirLoopHVAC / PlantLoop / CondenserLoop (supply <-> demand sides),
Connector:Splitter / Connector:Mixer (via Branch objects) and ZoneHVAC:EquipmentConnections (zones as
pseudo-components). No node or object names are assumed anywhere.

`HVACGraph.serving(zone)` traces upstream from a zone and returns the components that supply it:
terminal unit, air-loop equipment, return path, and the plant equipment feeding its water coils.
"""
from __future__ import annotations

import logging
from collections import deque

import networkx as nx

from ..ontology import Medium, Port, SimComponent

log = logging.getLogger(__name__)

EXCLUDE_EXACT = {
    "AirLoopHVAC", "PlantLoop", "CondenserLoop", "Branch", "BranchList", "Connector:Splitter", "Connector:Mixer",
    "ConnectorList", "NodeList", "OutdoorAir:NodeList", "OutdoorAir:Node", "AirLoopHVAC:ControllerList",
    "AirLoopHVAC:OutdoorAirSystem", "AirLoopHVAC:OutdoorAirSystem:EquipmentList", "AirLoopHVAC:SupplyPath",
    "AirLoopHVAC:ReturnPath", "ZoneHVAC:EquipmentList", "ZoneHVAC:AirDistributionUnit",
    "ZoneHVAC:EquipmentConnections", "PlantEquipmentList", "CondenserEquipmentList", "Zone", "ZoneList",
}
EXCLUDE_PREFIX = ("SetpointManager:", "Controller:", "AvailabilityManager", "Sizing:", "PlantEquipmentOperation",
                  "CondenserEquipmentOperation", "Output", "EnergyManagementSystem", "ZoneControl", "ThermostatSetpoint",
                  "Schedule", "HVACTemplate", "SurfaceProperty", "RoomAir", "ZoneProperty", "Daylighting",
                  "DesignSpecification", "Curve:", "Table:", "FaultModel:", "PythonPlugin", "ExternalInterface")
PORT_OVERRIDES = {
    "OutdoorAir:Mixer": dict(inp=["outdoor_air_stream_node_name", "return_air_stream_node_name"],
                             out=["mixed_air_node_name", "relief_air_stream_node_name"]),
}
SKIP_KEY_WORDS = ("sensor", "setpoint", "control_node", "zone_node_name", "zone_air_node_name", "reference")

CLASS_MAP = [
    ("AirTerminal:SingleDuct:VAV", "VAVTerminalUnit"), ("AirTerminal:", "AirTerminalUnit"),
    ("ZoneHVAC:FourPipeFanCoil", "FanCoilUnit"), ("ZoneHVAC:PackagedTerminal", "PackagedTerminalUnit"),
    ("ZoneHVAC:", "AirTerminalUnit"), ("Fan:", "Fan"), ("Coil:Cooling:DX", "DXCoolingCoil"),
    ("Coil:Cooling", "CoolingCoil"), ("Coil:Heating:Water", "HotWaterCoil"), ("Coil:Heating", "HeatingCoil"),
    ("OutdoorAir:Mixer", "OutdoorAirMixer"), ("AirLoopHVAC:ZoneSplitter", "AirSplitter"),
    ("AirLoopHVAC:ZoneMixer", "AirMixer"), ("AirLoopHVAC:ReturnPlenum", "ReturnPlenumComponent"),
    ("AirLoopHVAC:Unitary", "UnitarySystem"), ("CoilSystem:", "UnitarySystem"), ("Chiller", "Chiller"),
    ("DistrictCooling", "Chiller"), ("Boiler", "Boiler"), ("DistrictHeating", "Boiler"), ("WaterHeater:", "Boiler"),
    ("Pump:", "Pump"), ("HeaderedPumps", "Pump"), ("CoolingTower", "CoolingTower"), ("Pipe:", "PlantPipe"),
]


def classify(sim_type: str) -> str:
    return next((c for prefix, c in CLASS_MAP if sim_type.startswith(prefix)), "OtherComponent")


def _walk(obj, key=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, k)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)
    else:
        yield key, obj


def _excluded(cls: str) -> bool:
    return cls in EXCLUDE_EXACT or cls.startswith(EXCLUDE_PREFIX)


class HVACGraph:
    ROLE_ORDER = ["terminal", "zone_equipment", "air_supply", "plant_supply", "condenser_supply",
                  "plant_demand", "condenser_demand", "air_distribution", "other"]
    CONTAINERS = ("UnitarySystem", "FanCoilUnit", "PackagedTerminalUnit")

    def __init__(self, model) -> None:
        self.m = model
        self.G = nx.DiGraph()
        self.comps: dict[str, SimComponent] = {}
        self.nodelists = {n.upper(): [x["node_name"].upper() for x in f.get("nodes", [])]
                          for n, f in model.objs("NodeList").items()}
        self.branches = self._branches()
        self.water_nodes: set[str] = set()
        self.member: dict[str, list[tuple[str, str]]] = {}   # component key -> [(role, system), ...]
        self.zone_inlets: dict[str, list[str]] = {}
        self._membership()
        self._components()
        self._inherit_from_containers()
        self._implicit_links()
        self._zones()

    # ------------------------------------------------------------------ helpers
    def expand(self, name) -> list[str]:
        if not name:
            return []
        return self.nodelists.get(name.upper(), [name.upper()])

    @staticmethod
    def key(cls: str, name: str) -> str:
        return f"{cls}:{name}".upper()

    def _branches(self) -> dict:
        out = {}
        for bname, b in self.m.objs("Branch").items():
            comps = [(c["component_object_type"], c["component_name"], c["component_inlet_node_name"].upper(),
                      c["component_outlet_node_name"].upper()) for c in b.get("components", [])]
            if comps:
                out[bname.upper()] = dict(comps=comps, first=comps[0][2], last=comps[-1][3])
        return out

    def _branch_list(self, name) -> list[str]:
        _, bl = self.m.obj("BranchList", name or "")
        return [b["branch_name"].upper() for b in (bl or {}).get("branches", [])]

    def _add_member(self, key, role, system):
        lst = self.member.setdefault(key, [])
        if (role, system) not in lst:
            lst.append((role, system))

    def _membership(self) -> None:
        def mark(branch_names, role, system, water):
            for bn in branch_names:
                for typ, nm, i, o in self.branches.get(bn, {}).get("comps", []):
                    self._add_member(self.key(typ, nm), role, system)
                    if water:
                        self.water_nodes.update((i, o))
                    if typ == "AirLoopHVAC:OutdoorAirSystem":
                        _, oas = self.m.obj("AirLoopHVAC:OutdoorAirSystem", nm)
                        _, eql = self.m.obj("AirLoopHVAC:OutdoorAirSystem:EquipmentList",
                                            (oas or {}).get("outdoor_air_equipment_list_name", ""))
                        eql = eql or {}
                        for i in range(1, 20):                       # numbered fields in epJSON
                            t, n = eql.get(f"component_{i}_object_type"), eql.get(f"component_{i}_name")
                            if t and n:
                                self._add_member(self.key(t, n), role, system)
        for name, f in self.m.objs("PlantLoop").items():
            mark(self._branch_list(f.get("plant_side_branch_list_name")), "plant_supply", name, True)
            mark(self._branch_list(f.get("demand_side_branch_list_name")), "plant_demand", name, True)
        for name, f in self.m.objs("CondenserLoop").items():
            mark(self._branch_list(f.get("condenser_side_branch_list_name")), "condenser_supply", name, True)
            mark(self._branch_list(f.get("demand_side_branch_list_name")), "condenser_demand", name, True)
        for name, f in self.m.objs("AirLoopHVAC").items():
            mark(self._branch_list(f.get("branch_list_name")), "air_supply", name, False)
        for _, f in self.m.objs("ZoneHVAC:AirDistributionUnit").items():
            self._add_member(self.key(f["air_terminal_object_type"], f["air_terminal_name"]), "terminal", "")
        for _, f in self.m.objs("ZoneHVAC:EquipmentList").items():
            for item in f.get("equipment", []):
                t = item.get("zone_equipment_object_type", "")
                if t and t != "ZoneHVAC:AirDistributionUnit":
                    self._add_member(self.key(t, item["zone_equipment_name"]), "zone_equipment", "")

    def _components(self) -> None:
        for cls in self.m.classes():
            if _excluded(cls):
                continue
            for name, f in self.m.objs(cls).items():
                ports = []
                ov = PORT_OVERRIDES.get(cls)
                for k, v in _walk(f):
                    if not isinstance(v, str) or "node" not in k or any(w in k for w in SKIP_KEY_WORDS):
                        continue
                    if ov:
                        d = "in" if k in ov["inp"] else "out" if k in ov["out"] else None
                    else:
                        d = "in" if "inlet" in k else "out" if "outlet" in k else None
                    if d:
                        for node in self.expand(v):
                            ports.append(Port(k, node, d))
                if not ports:
                    continue
                key = self.key(cls, name)
                mem = self.member.get(key, [])
                if not mem and classify(cls) in ("AirSplitter", "AirMixer", "ReturnPlenumComponent"):
                    mem = [("air_distribution", "")]
                role, system = min(mem, key=lambda m: self.ROLE_ORDER.index(m[0])) if mem else ("other", "")
                comp = SimComponent(id=key, cls=classify(cls), name=name, sim_type=cls, ports=ports,
                                    role=role, system=system)
                comp.attrs["memberships"] = mem
                for p in ports:
                    p.medium = Medium.WATER if p.node in self.water_nodes else Medium.AIR
                    if p.direction == "in":
                        self.G.add_edge("N:" + p.node, "C:" + key)
                    else:
                        self.G.add_edge("C:" + key, "N:" + p.node)
                self.comps[key] = comp

    def _inherit_from_containers(self) -> None:
        """Children of a container (CoilSystem, UnitarySystem, fan coil, PTAC) share its nodes; they inherit its
        system memberships so that e.g. a DX coil inside a CoilSystem counts as air-loop supply equipment."""
        for parent in [c for c in self.comps.values() if c.cls in self.CONTAINERS and c.attrs["memberships"]]:
            pn = {p.node for p in parent.ports}
            for child in self.comps.values():
                if child is parent or child.cls in self.CONTAINERS or not ({p.node for p in child.ports} & pn):
                    continue
                for m in parent.attrs["memberships"]:
                    if m not in child.attrs["memberships"]:
                        child.attrs["memberships"].append(m)
                if child.role in ("other", "plant_demand"):
                    child.role, child.system = min(child.attrs["memberships"], key=lambda m: self.ROLE_ORDER.index(m[0]))

    def _link(self, a, b, kind, loop):
        for x, y in zip(self.expand(a), self.expand(b)):
            self.G.add_edge("N:" + x, "N:" + y, kind=kind, loop=loop)

    def _implicit_links(self) -> None:
        for name, f in self.m.objs("AirLoopHVAC").items():
            self._link(f.get("supply_side_outlet_node_names"), f.get("demand_side_inlet_node_names"), "supply", name)
            self._link(f.get("demand_side_outlet_node_name"), f.get("supply_side_inlet_node_name"), "return_air", name)
        for cls, sup_in, sup_out in (("PlantLoop", "plant_side_inlet_node_name", "plant_side_outlet_node_name"),
                                     ("CondenserLoop", "condenser_side_inlet_node_name", "condenser_side_outlet_node_name")):
            for name, f in self.m.objs(cls).items():
                self._link(f.get(sup_out), f.get("demand_side_inlet_node_name"), "supply", name)
                self._link(f.get("demand_side_outlet_node_name"), f.get(sup_in), "return_water", name)
        for name, f in self.m.objs("Connector:Splitter").items():
            src = self.branches.get(f["inlet_branch_name"].upper())
            for b in f.get("branches", []):
                dst = self.branches.get(b["outlet_branch_name"].upper())
                if src and dst:
                    self.G.add_edge("N:" + src["last"], "N:" + dst["first"], kind="splitter", loop=name)
        for name, f in self.m.objs("Connector:Mixer").items():
            dst = self.branches.get(f["outlet_branch_name"].upper())
            for b in f.get("branches", []):
                src = self.branches.get(b["inlet_branch_name"].upper())
                if src and dst:
                    self.G.add_edge("N:" + src["last"], "N:" + dst["first"], kind="mixer", loop=name)

    def _zones(self) -> None:
        for _, f in self.m.objs("ZoneHVAC:EquipmentConnections").items():
            z = "Z:" + f["zone_name"].upper()
            inlets = self.expand(f.get("zone_air_inlet_node_or_nodelist_name"))
            self.zone_inlets[f["zone_name"].upper()] = inlets
            for n in inlets:
                self.G.add_edge("N:" + n, z)
            for n in self.expand(f.get("zone_return_air_node_or_nodelist_name")):
                self.G.add_edge(z, "N:" + n)

    # ------------------------------------------------------------------ queries
    def serving(self, zone: str) -> dict:
        """Upstream trace from a zone. Stops at other zones and does not follow plant return links, so
        parallel demand branches serving other zones are not pulled in."""
        start = "Z:" + zone.upper()
        if start not in self.G:
            raise KeyError(f"zone {zone} has no HVAC connections")
        dist = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            for pred in self.G.predecessors(cur):
                e = self.G.edges[pred, cur]
                if e.get("kind") == "return_water" or pred in dist:
                    continue
                if pred.startswith("Z:"):
                    continue
                dist[pred] = dist[cur] + (1 if pred.startswith("C:") else 0)
                q.append(pred)
        comps = sorted(((self.comps[k[2:]], d) for k, d in dist.items() if k.startswith("C:")), key=lambda x: x[1])
        loops = sorted({c.system for c, _ in comps if c.system})
        return dict(zone=zone, components=comps, loops=loops, inlet_nodes=self.zone_inlets.get(zone.upper(), []))

    def has_role(self, comp: SimComponent, role: str) -> bool:
        return any(r == role for r, _ in comp.attrs.get("memberships", [])) or comp.role == role

    def plant_sources(self, comp: SimComponent) -> list[SimComponent]:
        """Plant supply equipment (chiller, boiler, district source) feeding the water inlet of `comp`."""
        start = [p for p in comp.ports if p.direction == "in" and p.medium == Medium.WATER]
        seen, q, out = set(), deque("N:" + p.node for p in start), []
        while q:
            cur = q.popleft()
            for pred in self.G.predecessors(cur):
                if pred in seen or self.G.edges[pred, cur].get("kind") == "return_water" or pred.startswith("Z:"):
                    continue
                seen.add(pred); q.append(pred)
                if pred.startswith("C:"):
                    c = self.comps[pred[2:]]
                    if c.cls in ("Chiller", "Boiler") and self.has_role(c, "plant_supply"):
                        out.append(c)
        return out

    def loop_members(self, system: str, role: str, cls: str | None = None) -> list[SimComponent]:
        return [c for c in self.comps.values() if (role, system) in c.attrs.get("memberships", [])
                and (cls is None or c.cls == cls)]

    def feeds_edges(self, keys: set[str]) -> list[tuple[str, str]]:
        """Component-to-component flow edges among `keys` (through shared or linked nodes)."""
        out = set()
        for k in keys:
            for _, n in self.G.out_edges("C:" + k):
                stack, seen = [n], set()
                while stack:
                    x = stack.pop()
                    for _, y in self.G.out_edges(x):
                        if y.startswith("C:") and y[2:] in keys and y[2:] != k:
                            out.add((k, y[2:]))
                        elif y.startswith("N:") and y not in seen:
                            seen.add(y); stack.append(y)
        return sorted(out)

    def summary(self, zone: str) -> str:
        s = self.serving(zone)
        lines = [f"Zone {zone}: inlet nodes {s['inlet_nodes']}; loops {s['loops']}"]
        for c, d in s["components"]:
            lines.append(f"  [{d:2d}] {c.cls:22s} {c.sim_type:40s} {c.name}  (role={c.role}{', ' + c.system if c.system else ''})")
        return "\n".join(lines)
