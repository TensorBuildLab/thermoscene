"""
Generic HVAC topology from an EnergyPlus model.

Every object that names inlet/outlet nodes becomes a SimComponent; components sharing a node are
connected. Implicit links come from AirLoopHVAC / PlantLoop / CondenserLoop (supply <-> demand sides),
Connector:Splitter / Connector:Mixer (via Branch objects) and ZoneHVAC:EquipmentConnections (zones as
pseudo-components; every item of a zone's equipment list is linked to its zone, so baseboards, radiant panels and
exhaust fans count too). Declared equipment without nodes of its own (energy recovery ventilators, heat-exchanger-
assisted coil systems, electric baseboards) becomes a component with its branch's and its parts' ports.
No node or object names are assumed anywhere; object types in fields are matched case-insensitively.

`HVACGraph.serving(zone)` traces upstream from a zone and returns the components that supply it:
terminal unit, zone equipment, air-loop equipment (all of each air loop the trace passes, relief side included),
return path, the plant equipment feeding its water coils, and the parts each of them declares.
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
                  "DesignSpecification", "Curve:", "Table:", "FaultModel:", "PythonPlugin", "ExternalInterface",
                  # performance data and control objects referenced by equipment; they carry no flow
                  "CoilPerformance:", "UnitarySystemPerformance:", "CoolingTowerPerformance:", "ChillerHeaterPerformance:",
                  "HeatExchanger:Desiccant:BalancedFlow:PerformanceDataType", "ZoneHVAC:EnergyRecoveryVentilator:Controller",
                  "ZoneHVAC:OutdoorAirUnit:EquipmentList",
                  "AirflowNetwork")          # airflow-network descriptions of components that exist as HVAC objects
# Node fields whose key does not say 'inlet'/'outlet' (direction from the IDD field descriptions); other fields of the
# class follow the key rule.
PORT_OVERRIDES = {
    "OutdoorAir:Mixer": dict(inp=["outdoor_air_stream_node_name", "return_air_stream_node_name"],
                             out=["mixed_air_node_name", "relief_air_stream_node_name"]),
    "ZoneHVAC:IdealLoadsAirSystem": dict(inp=["zone_exhaust_air_node_name"], out=["zone_supply_air_node_name"]),
    "ZoneHVAC:HybridUnitaryHVAC": dict(inp=["return_air_node_name", "outdoor_air_node_name"],
                                       out=["supply_air_node_name", "relief_node_name"]),
    "ZoneHVAC:VentilatedSlab": dict(inp=["return_air_node_name", "outdoor_air_node_name", "slab_in_node_name"],
                                    out=["zone_supply_air_node_name", "relief_air_node_name"]),
    "ZoneHVAC:UnitVentilator": dict(inp=["outdoor_air_node_name"], out=["exhaust_air_node_name", "mixed_air_node_name"]),
    "ZoneHVAC:EvaporativeCoolerUnit": dict(inp=["zone_relief_air_node_name"], out=[]),
    "ZoneHVAC:OutdoorAirUnit": dict(inp=["outdoor_air_node_name"], out=[]),
    "WaterHeater:HeatPump:PumpedCondenser": dict(inp=["outdoor_air_node_name"], out=["exhaust_air_node_name"]),
    "WaterHeater:HeatPump:WrappedCondenser": dict(inp=["outdoor_air_node_name"], out=["exhaust_air_node_name"]),
    # the bypass duct: coil train outlet -> (splitter) -> unit outlet; unit inlet + bypass -> (mixer) -> coil train
    "AirLoopHVAC:UnitaryHeatCool:VAVChangeoverBypass": dict(inp=["bypass_duct_splitter_node_name"],
                                                           out=["bypass_duct_mixer_node_name"]),
}


def port_direction(cls: str, key: str) -> str | None:
    ov = PORT_OVERRIDES.get(cls, {})
    if key in ov.get("inp", ()):
        return "in"
    if key in ov.get("out", ()):
        return "out"
    return "in" if "inlet" in key else "out" if "outlet" in key else None


SKIP_KEY_WORDS =("sensor", "setpoint", "control_node", "zone_node_name", "zone_air_node_name", "reference")
# '<x>_name' fields of a container that never name one of its parts
NOT_CHILD_KEY_WORDS = ("zone", "space", "surface", "schedule", "curve", "node", "controller", "manager", "specification",
                       "list", "table")

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


_EXCLUDE_UP = {c.upper() for c in EXCLUDE_EXACT}
_EXCLUDE_PREFIX_UP = tuple(p.upper() for p in EXCLUDE_PREFIX)


def _excluded(cls: str) -> bool:
    u = cls.upper()                              # object types in fields are case-insensitive
    return u in _EXCLUDE_UP or u.startswith(_EXCLUDE_PREFIX_UP)


# equipment lists: not components themselves, but their <x>_object_type / <x>_name pairs declare equipment
LIST_CLASSES = ("AirLoopHVAC:OutdoorAirSystem:EquipmentList", "ZoneHVAC:OutdoorAirUnit:EquipmentList")


def object_refs(f: dict) -> list[tuple[str, str]]:
    """(type, name) of the objects a field set names through <x>_object_type + <x>_name pairs, also spelt
    <x>_object_name (VRF terminal units) or <y>_<x>_name (window air conditioner: cooling_coil_object_type +
    dx_cooling_coil_name)."""
    out = []
    for k, v in f.items():
        if k.endswith("_object_type") and isinstance(v, str):
            base = k[:-len("_object_type")]
            n = f.get(base + "_name", f.get(base + "_object_name"))
            if not isinstance(n, str):
                cands = [x for x in f if x.endswith("_" + base + "_name") and isinstance(f[x], str)]
                n = f[cands[0]] if len(cands) == 1 else None
            if isinstance(n, str):
                out.append((v, n))
    return out


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
                    if typ.upper() == "AIRLOOPHVAC:OUTDOORAIRSYSTEM":
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
            # epJSON names this field condenser_demand_side_branch_list_name (PlantLoop: demand_side_branch_list_name)
            mark(self._branch_list(f.get("condenser_demand_side_branch_list_name") or f.get("demand_side_branch_list_name")),
                 "condenser_demand", name, True)
        for name, f in self.m.objs("AirLoopHVAC").items():
            mark(self._branch_list(f.get("branch_list_name")), "air_supply", name, False)
        for _, f in self.m.objs("ZoneHVAC:AirDistributionUnit").items():
            self._add_member(self.key(f["air_terminal_object_type"], f["air_terminal_name"]), "terminal", "")
        for _, f in self.m.objs("ZoneHVAC:EquipmentList").items():
            for item in f.get("equipment", []):
                t = item.get("zone_equipment_object_type", "")
                if t and t.upper() != "ZONEHVAC:AIRDISTRIBUTIONUNIT":
                    self._add_member(self.key(t, item["zone_equipment_name"]), "zone_equipment", "")

    def _components(self) -> None:
        for cls in self.m.classes():
            if _excluded(cls):
                continue
            for name, f in self.m.objs(cls).items():
                ports = []
                for k, v in _walk(f):
                    if not isinstance(v, str) or "node" not in k or any(w in k for w in SKIP_KEY_WORDS):
                        continue
                    d = port_direction(cls, k)
                    if d:
                        for node in self.expand(v):
                            ports.append(Port(k, node, d))
                if ports:
                    self._add_component(cls, name, ports)
        self._portless_components()

    def _add_component(self, cls, name, ports, **attrs) -> SimComponent:
        key = self.key(cls, name)
        mem = self.member.get(key, [])
        if not mem and classify(cls) in ("AirSplitter", "AirMixer", "ReturnPlenumComponent"):
            mem = [("air_distribution", "")]
        role, system = min(mem, key=lambda m: self.ROLE_ORDER.index(m[0])) if mem else ("other", "")
        comp = SimComponent(id=key, cls=classify(cls), name=name, sim_type=cls, ports=ports, role=role, system=system)
        comp.attrs["memberships"] = mem
        comp.attrs.update(attrs)
        for p in ports:
            p.medium = Medium.WATER if p.node in self.water_nodes else Medium.AIR
            if p.direction == "in":
                self.G.add_edge("N:" + p.node, "C:" + key)
            else:
                self.G.add_edge("C:" + key, "N:" + p.node)
        self.comps[key] = comp
        return comp

    def _declared(self) -> dict[str, tuple]:
        """Objects the model declares as HVAC equipment: branch members (with the branch's inlet/outlet nodes), zone
        equipment (air terminals of air distribution units included) and the children an object names through
        <x>_object_type / <x>_name field pairs. {key: (type, name, [branch ports])}."""
        out: dict[str, tuple] = {}

        def put(t, n, ports=()):
            if t and n and not _excluded(t):
                t = self.m._class_key(t)                    # the class's own spelling (fields may differ in case)
                k = self.key(t, n)
                prev = out.get(k, (t, n, []))
                out[k] = (t, n, prev[2] + list(ports))
        for b in self.branches.values():
            for t, n, i, o in b["comps"]:
                put(t, n, [Port("branch_inlet_node", x, "in") for x in self.expand(i)]
                    + [Port("branch_outlet_node", x, "out") for x in self.expand(o)])
        for f in self.m.objs("ZoneHVAC:EquipmentList").values():
            for item in f.get("equipment", []):
                put(*self._zone_equipment(item))
        for cls in self.m.classes():
            if _excluded(cls) and cls not in LIST_CLASSES:
                continue
            for f in self.m.objs(cls).values():
                for t, n in object_refs(f):
                    put(t, n)
        return out

    def _zone_equipment(self, item) -> tuple[str, str]:
        """(type, name) of a ZoneHVAC:EquipmentList item; an air distribution unit stands for its air terminal."""
        t, n = item.get("zone_equipment_object_type", ""), item.get("zone_equipment_name", "")
        if t.upper() == "ZONEHVAC:AIRDISTRIBUTIONUNIT":
            _, adu = self.m.obj(t, n)
            t, n = (adu or {}).get("air_terminal_object_type", ""), (adu or {}).get("air_terminal_name", "")
        return t, n

    def _portless_components(self) -> None:
        """Declared equipment that names no node of its own: a container such as an energy recovery ventilator or a
        heat-exchanger-assisted coil system (its ports: the branch's nodes and its children's ports), or zone equipment
        acting on the zone without air nodes (electric baseboard, radiant panel; no ports, linked to its zone)."""
        declared = self._declared()
        zone_eq = {self.key(*self._zone_equipment(i)) for f in self.m.objs("ZoneHVAC:EquipmentList").values()
                   for i in f.get("equipment", [])}
        names = {}
        for c in self.comps.values():
            names.setdefault(c.name.upper(), []).append(c.id)
        pending = [k for k in declared if k not in self.comps and self.m.obj(declared[k][0], declared[k][1])[1] is not None]
        for _ in range(4):                                 # nested containers resolve from the inside out
            left = []
            for k in pending:
                t, n, bports = declared[k]
                _, f = self.m.obj(t, n)
                kids = {self.key(ct, cn) for ct, cn in object_refs(f)}
                for fk, v in f.items():                      # children named without a type field
                    if (isinstance(v, str) and fk.endswith("_name") and not any(w in fk for w in NOT_CHILD_KEY_WORDS)
                            and len(names.get(v.upper(), [])) == 1):
                        kids.add(names[v.upper()][0])
                ports = [Port(p.name, p.node, p.direction) for p in bports]
                for kid in sorted(kids):
                    if kid in self.comps:
                        ports += [Port(f"{kid.lower()}:{p.name}", p.node, p.direction) for p in self.comps[kid].ports]
                if ports or k in zone_eq:
                    n = self.m.obj(t, n)[0]                     # the object's own spelling of its name
                    c = self._add_component(t, n, ports, container=bool(kids), children=sorted(kids))
                    names.setdefault(n.upper(), []).append(c.id)
                else:
                    left.append(k)
            if len(left) == len(pending):
                break
            pending = left

    def _children(self, parent: SimComponent) -> set[str]:
        """Component keys a container declares through <x>_object_type / <x>_name field pairs (its coils, fans, mixer)."""
        _, f = self.m.obj(parent.sim_type, parent.name)
        return {self.key(t, n) for t, n in object_refs(f or {})}

    def _inherit_from_containers(self) -> None:
        """Children of a container (CoilSystem, UnitarySystem, fan coil, PTAC) inherit its system memberships, so that
        e.g. a DX coil inside a CoilSystem or a unitary system counts as air-loop supply equipment. A child is a
        component the container declares as its own or one that shares a node with it (a blow-through or draw-through
        middle component shares no node with the container, only the declaration links it)."""
        is_container = lambda c: c.cls in self.CONTAINERS or c.attrs.get("container")
        for _ in range(3):                                 # a container inside a container (coil system in a unitary)
            changed = False
            for parent in [c for c in self.comps.values() if is_container(c) and c.attrs["memberships"]]:
                pn = {p.node for p in parent.ports}
                declared = self._children(parent) | set(parent.attrs.get("children", []))
                for child in self.comps.values():
                    if child is parent or (is_container(child) and child.id not in declared):
                        continue
                    if child.id not in declared and not ({p.node for p in child.ports} & pn):
                        continue
                    for m in parent.attrs["memberships"]:
                        if m not in child.attrs["memberships"]:
                            child.attrs["memberships"].append(m); changed = True
                    if child.role in ("other", "plant_demand"):
                        child.role, child.system = min(child.attrs["memberships"], key=lambda m: self.ROLE_ORDER.index(m[0]))
            if not changed:
                break

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
            # every item of the zone's equipment list acts on the zone, with or without an air inlet node
            # (baseboards, radiant panels, exhaust fans, ideal loads)
            _, eql = self.m.obj("ZoneHVAC:EquipmentList", f.get("zone_conditioning_equipment_list_name") or "")
            for item in (eql or {}).get("equipment", []):
                k = self.key(*self._zone_equipment(item))
                if k in self.comps:
                    self.G.add_edge("C:" + k, z, kind="zone_equipment")

    # ------------------------------------------------------------------ queries
    def _upstream(self, dist: dict, frontier: list) -> None:
        """Breadth-first walk against the flow from `frontier`; stops at zones and does not follow plant return links."""
        q = deque(frontier)
        while q:
            cur = q.popleft()
            for pred in self.G.predecessors(cur):
                e = self.G.edges[pred, cur]
                if e.get("kind") == "return_water" or pred in dist or pred.startswith("Z:"):
                    continue
                dist[pred] = dist[cur] + (1 if pred.startswith("C:") else 0)
                q.append(pred)

    def serving(self, zone: str) -> dict:
        """Upstream trace from a zone. Stops at other zones and does not follow plant return links, so
        parallel demand branches serving other zones are not pulled in."""
        start = "Z:" + zone.upper()
        if start not in self.G:
            raise KeyError(f"zone {zone} has no HVAC connections")
        dist = {start: 0}
        frontier = [start]
        for _ in range(10):
            self._upstream(dist, frontier)
            frontier = []
            # the rest of every air loop the chain passes through: supply-side equipment off the supply air path
            # (relief fan, exhaust side of heat recovery) belongs to the same air handler
            loops = {s for k in dist if k.startswith("C:") for r, s in self.comps[k[2:]].attrs.get("memberships", [])
                     if r == "air_supply" and s}
            for s in sorted(loops):
                d = max(v for k, v in dist.items() if k.startswith("C:") and ("air_supply", s) in self.comps[k[2:]].attrs["memberships"])
                for c in self.loop_members(s, "air_supply"):
                    if "C:" + c.id not in dist:
                        dist["C:" + c.id] = d + 1; frontier.append("C:" + c.id)
            # parts a chain component declares through <x>_object_type / <x>_name pairs (a heat pump water heater's
            # tank and fan, a desuperheater's heat source) belong to the same unit even where no node links them
            for k in [k for k in dist if k.startswith("C:")]:
                c = self.comps[k[2:]]
                for kid in sorted(self._children(c) | set(c.attrs.get("children", []))):
                    if kid in self.comps and "C:" + kid not in dist:
                        dist["C:" + kid] = dist[k] + 1; frontier.append("C:" + kid)
            if not frontier:                               # what feeds the added components is traced too
                break
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
                if not n.startswith("N:"):                 # zone-equipment links end at the zone
                    continue
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
