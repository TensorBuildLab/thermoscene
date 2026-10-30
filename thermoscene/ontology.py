"""
thermoscene ontology
====================

The pipeline's own conceptual model. Every stage (EnergyPlus adapter, generators, thermal models,
USD writer, renderers) reads and writes instances of these concepts, never model-specific names.

Concepts
--------
Space         a volume with its own thermal environment: a conditioned zone, a plenum, the outdoors
Surface       a heat-transfer surface simulated by EnergyPlus (wall, floor, ceiling, roof, window, door)
Opening       a sub-surface cut out of a host surface (window, door, injected envelope defect)
SimComponent  an EnergyPlus HVAC object (coil, fan, chiller, terminal unit, ...) - data only, no geometry
Element       a physical, visible object generated for the scene (duct segment, pipe, valve, diffuser,
              equipment casing). An Element `represents` one or more SimComponents.
Port          a connection point of a SimComponent, bound to an EnergyPlus node; carries a Medium
Stream        the time series (temperature, mass flow) at a Port's node
Camera        a thermal sensor with intrinsics, pose and the Space whose environment it observes
Fault         an injected deviation (envelope defect, missing duct insulation, EnergyPlus FaultModel)

Relations
---------
locatedIn(Element|Surface|Camera -> Space)       hosts(Surface -> Opening)
represents(Element -> SimComponent)              hasPort(SimComponent -> Port)
connectedTo(Element -> Element)                  feeds(SimComponent -> SimComponent)  (flow direction)
servesSpace(SimComponent -> Space)               adjacentTo(Surface -> Surface)
observes(Camera -> Space)                        affects(Fault -> Surface|Element)

Provenance (attached to every quantity, written to USD as `<namespace>:provenance:*`)
----------
SIMULATED         computed by EnergyPlus (surface / zone / node results, autosized capacities)
PHYSICS           derived from simulated values with a physical model (conduction through insulation)
ENGINEERING_RULE  standard design practice (design velocities, sizing rules, code clearances)
HEURISTIC         simple empirical relation with uncalibrated parameters (motor temperature rise)
ASSUMED_LAYOUT    placement / routing chosen by a layout rule, not observed
USER              given explicitly in the case configuration
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    SIMULATED = "simulated"
    PHYSICS = "physics"
    ENGINEERING_RULE = "engineering_rule"
    HEURISTIC = "heuristic"
    ASSUMED_LAYOUT = "assumed_layout"
    USER = "user"


class Medium(str, Enum):
    AIR = "air"
    WATER = "water"
    REFRIGERANT = "refrigerant"
    NONE = "none"


# ---------------------------------------------------------------------------------------------
# Class hierarchy with external mappings. Brick: https://brickschema.org, 223P: ASHRAE Std 223P.
# ---------------------------------------------------------------------------------------------
CLASSES: dict[str, dict[str, str]] = {
    # spaces and envelope
    "Space":                 dict(parent="",            brick="brick:Space",                    s223="s223:PhysicalSpace"),
    "Zone":                  dict(parent="Space",       brick="brick:HVAC_Zone",                s223="s223:DomainSpace"),
    "Plenum":                dict(parent="Space",       brick="brick:Space",                    s223="s223:PhysicalSpace"),
    "Outdoors":              dict(parent="Space",       brick="brick:Outside",                  s223="s223:PhysicalSpace"),
    "Surface":               dict(parent="",            brick="",                               s223=""),
    "Opening":               dict(parent="Surface",     brick="",                               s223=""),
    # simulated HVAC components
    "SimComponent":          dict(parent="",            brick="brick:Equipment",                s223="s223:Equipment"),
    "AirTerminalUnit":       dict(parent="SimComponent", brick="brick:Terminal_Unit",           s223="s223:TerminalUnit"),
    "VAVTerminalUnit":       dict(parent="AirTerminalUnit", brick="brick:VAV",                  s223="s223:SingleDuctTerminal"),
    "FanCoilUnit":           dict(parent="AirTerminalUnit", brick="brick:FCU",                  s223="s223:FanCoilUnit"),
    "PackagedTerminalUnit":  dict(parent="AirTerminalUnit", brick="brick:Terminal_Unit",        s223="s223:TerminalUnit"),
    "Fan":                   dict(parent="SimComponent", brick="brick:Fan",                     s223="s223:Fan"),
    "CoolingCoil":           dict(parent="SimComponent", brick="brick:Cooling_Coil",            s223="s223:CoolingCoil"),
    "DXCoolingCoil":         dict(parent="CoolingCoil",  brick="brick:Cooling_Coil",            s223="s223:CoolingCoil"),
    "HeatingCoil":           dict(parent="SimComponent", brick="brick:Heating_Coil",            s223="s223:HeatingCoil"),
    "HotWaterCoil":          dict(parent="HeatingCoil",  brick="brick:Hot_Water_Coil",          s223="s223:HeatingCoil"),
    "OutdoorAirMixer":       dict(parent="SimComponent", brick="brick:Economizer",              s223="s223:Equipment"),
    "AirSplitter":           dict(parent="SimComponent", brick="",                              s223="s223:Junction"),
    "AirMixer":              dict(parent="SimComponent", brick="",                              s223="s223:Junction"),
    "ReturnPlenumComponent": dict(parent="SimComponent", brick="",                              s223="s223:Junction"),
    "Chiller":               dict(parent="SimComponent", brick="brick:Chiller",                 s223="s223:Chiller"),
    "Boiler":                dict(parent="SimComponent", brick="brick:Boiler",                  s223="s223:Boiler"),
    "Pump":                  dict(parent="SimComponent", brick="brick:Pump",                    s223="s223:Pump"),
    "CoolingTower":          dict(parent="SimComponent", brick="brick:Cooling_Tower",           s223="s223:CoolingTower"),
    "UnitarySystem":         dict(parent="SimComponent", brick="brick:RTU",                     s223="s223:Equipment"),
    "PlantPipe":             dict(parent="SimComponent", brick="",                              s223="s223:Pipe"),
    "OtherComponent":        dict(parent="SimComponent", brick="brick:Equipment",               s223="s223:Equipment"),
    # note: DistrictCooling / DistrictHeating / WaterHeater are mapped to Chiller / Boiler as plant sources
    # generated physical elements
    "Element":               dict(parent="",            brick="",                               s223=""),
    "Duct":                  dict(parent="Element",     brick="",                               s223="s223:Duct"),
    "Pipe":                  dict(parent="Element",     brick="",                               s223="s223:Pipe"),
    "Valve":                 dict(parent="Element",     brick="brick:Valve",                    s223="s223:Valve"),
    "Diffuser":              dict(parent="Element",     brick="brick:Diffuser",                 s223="s223:Diffuser"),
    "ReturnGrille":          dict(parent="Element",     brick="",                               s223="s223:Equipment"),
    "TerminalBox":           dict(parent="Element",     brick="brick:VAV",                      s223="s223:SingleDuctTerminal"),
    "CoilSection":           dict(parent="Element",     brick="",                               s223="s223:Equipment"),
    "EquipmentCasing":       dict(parent="Element",     brick="brick:Equipment",                s223="s223:Equipment"),
    "CondenserCoil":         dict(parent="Element",     brick="brick:Condenser",                s223="s223:Equipment"),
    "CondenserFan":          dict(parent="Element",     brick="brick:Condenser_Fan",            s223="s223:Fan"),
    "Motor":                 dict(parent="Element",     brick="brick:Motor",                    s223="s223:ElectricMotor"),
    "Louver":                dict(parent="Element",     brick="brick:Damper",                   s223="s223:Equipment"),
    "RoofSurface":           dict(parent="Element",     brick="brick:Roof",                     s223=""),
    "InsulationDefect":      dict(parent="Element",     brick="",                               s223=""),
    "Camera":                dict(parent="",            brick="brick:Camera",                   s223="s223:Sensor"),
}


def is_a(cls: str, ancestor: str) -> bool:
    while cls:
        if cls == ancestor:
            return True
        cls = CLASSES.get(cls, {}).get("parent", "")
    return False


# ---------------------------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------------------------
@dataclass
class Port:
    name: str                          # field name in the EnergyPlus object (e.g. "water_inlet_node_name")
    node: str                          # EnergyPlus node name (upper-cased)
    direction: str                     # "in" | "out"
    medium: Medium = Medium.AIR


@dataclass
class Thing:
    id: str
    cls: str
    name: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, tuple[Provenance, str]] = field(default_factory=dict)

    def prov(self, key: str, level: Provenance, note: str = "") -> None:
        self.provenance[key] = (level, note)


@dataclass
class Space(Thing):
    sim_zone: str | None = None        # EnergyPlus zone name (None for Outdoors)
    multiplier: int = 1
    z_floor: float = 0.0
    z_ceiling: float = 0.0


@dataclass
class Surface(Thing):
    space: str = ""
    sim_surface: str | None = None
    polygon: list[tuple[float, float, float]] = field(default_factory=list)       # outer ring, outward order
    holes: list[list[tuple[float, float, float]]] = field(default_factory=list)
    construction: str = ""
    boundary: str = ""                 # Outdoors | Adiabatic | Surface | Ground | ...
    adjacent: str | None = None        # EnergyPlus surface on the other side
    host: str | None = None            # for Openings
    emissivity: float = 0.9
    surface_type: str = ""             # Wall | Floor | Ceiling | Roof | Window | Door | Defect


@dataclass
class SimComponent(Thing):
    sim_type: str = ""                 # EnergyPlus object type
    ports: list[Port] = field(default_factory=list)
    system: str = ""                   # air loop / plant loop it belongs to
    role: str = ""                     # terminal | air_supply | air_return | plant_supply | plant_demand | zone
    sizes: dict[str, float] = field(default_factory=dict)   # EnergyPlus "Design Size ..." values

    def port(self, *keywords: str, direction: str | None = None) -> Port | None:
        for p in self.ports:
            if all(k in p.name for k in keywords) and (direction is None or p.direction == direction):
                return p
        return None


@dataclass
class Element(Thing):
    space: str = ""                    # Space id it is located in
    quads: list = field(default_factory=list)          # polygon faces (each a list of 3D points)
    represents: list[str] = field(default_factory=list)
    connected_to: list[str] = field(default_factory=list)
    medium: Medium = Medium.NONE
    stream_node: str | None = None     # EnergyPlus node whose fluid is inside this element
    thermal_model: str = ""            # key into the thermal model registry
    thermal_params: dict[str, Any] = field(default_factory=dict)
    material: str = ""                 # key into config materials


@dataclass
class Camera(Thing):
    space: str = ""
    eye: tuple[float, float, float] = (0, 0, 0)
    target: tuple[float, float, float] = (0, 0, 1)
    hfov_deg: float = 60.0
    resolution: tuple[int, int] = (320, 240)
    frames: list[str] = field(default_factory=list)


@dataclass
class Fault(Thing):
    kind: str = ""
    affects: str = ""
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Scene graph
# ---------------------------------------------------------------------------------------------
class SceneGraph:
    """In-memory instance graph. Time series live in `series` keyed by (owner id, quantity)."""

    def __init__(self) -> None:
        self.things: dict[str, Thing] = {}
        self.feeds: list[tuple[str, str]] = []          # SimComponent -> SimComponent
        self.series: dict[tuple[str, str], Any] = {}    # (thing id, quantity) -> numpy array
        self.time_labels: list[str] = []
        self.meta: dict[str, Any] = {}

    def add(self, thing: Thing) -> Thing:
        if thing.id in self.things:
            raise ValueError(f"duplicate id {thing.id}")
        self.things[thing.id] = thing
        return thing

    def get(self, id_: str) -> Thing:
        return self.things[id_]

    def of(self, kind: type, cls: str | None = None) -> list:
        return [t for t in self.things.values() if isinstance(t, kind) and (cls is None or is_a(t.cls, cls))]

    def set_series(self, owner: str, quantity: str, values, level: Provenance, note: str = "") -> None:
        self.series[(owner, quantity)] = values
        self.things[owner].prov(quantity, level, note)

    # ---------------- RDF (Turtle) export, for interoperability with Brick / 223P tooling
    def to_turtle(self, base: str = "urn:thermoscene:") -> str:
        pre = ["@prefix ts: <https://example.org/thermoscene#> .", "@prefix brick: <https://brickschema.org/schema/Brick#> .",
               "@prefix s223: <http://data.ashrae.org/standard223#> .", "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", ""]
        iri = lambda i: f"<{base}{i}>"
        lines = []
        for t in self.things.values():
            types = [f"ts:{t.cls}"] + [m for m in (CLASSES.get(t.cls, {}).get("brick"), CLASSES.get(t.cls, {}).get("s223")) if m]
            props = [f"a {', '.join(types)}", f'rdfs:label "{t.name or t.id}"']
            for attr, rel in (("space", "ts:locatedIn"),):
                v = getattr(t, attr, "")
                if v:
                    props.append(f"{rel} {iri(v)}")
            if isinstance(t, Element):
                props += [f"ts:represents {iri(r)}" for r in t.represents]
                props += [f"ts:connectedTo {iri(c)}" for c in t.connected_to]
                if t.stream_node:
                    props.append(f'ts:streamNode "{t.stream_node}"')
            if isinstance(t, Surface) and t.host:
                props.append(f"ts:hostedBy {iri(t.host)}")
            if isinstance(t, SimComponent):
                props.append(f'ts:simType "{t.sim_type}"')
                props += [f'ts:hasPort [ ts:node "{p.node}" ; ts:direction "{p.direction}" ; ts:medium "{p.medium.value}" ]'
                          for p in t.ports]
            for q, (lvl, _) in t.provenance.items():
                props.append(f'ts:provenance [ ts:quantity "{q}" ; ts:level "{lvl.value}" ]')
            lines.append(iri(t.id) + "\n    " + " ;\n    ".join(props) + " .")
        lines += [f"{iri(a)} ts:feeds {iri(b)} ." for a, b in self.feeds]
        return "\n".join(pre + lines) + "\n"
