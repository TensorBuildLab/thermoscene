# The thermoscene ontology

The pipeline is organised around its own small ontology. Every stage reads and writes instances of these
concepts; no stage refers to model-specific names. The same concepts appear in the USD output (prim layout,
`semantic:class`) and in the RDF export (`scene.ttl`).

## Concepts

```
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
```

## Relations

```
locatedIn(Element|Surface|Camera -> Space)       hosts(Surface -> Opening)
represents(Element -> SimComponent)              hasPort(SimComponent -> Port)
connectedTo(Element -> Element)                  feeds(SimComponent -> SimComponent)  (flow direction)
servesSpace(SimComponent -> Space)               adjacentTo(Surface -> Surface)
observes(Camera -> Space)                        affects(Fault -> Surface|Element)
```

## Provenance levels

Every quantity of every instance carries one level, written to USD as `<attribute>:provenance` and
`<attribute>:provenanceNote`, counted in `summary.json`, and exported in `scene.ttl`.

| Level | Meaning |
|---|---|
| `simulated` | computed by EnergyPlus (surface, zone and node results; autosized capacities) |
| `physics` | derived from simulated values with a physical model (conduction through insulation, film resistances) |
| `engineering_rule` | standard design practice (design velocities, sizing rules, catalogue minimums) |
| `heuristic` | simple empirical relation with uncalibrated parameters (motor temperature rise, ceiling jets) |
| `assumed_layout` | placement / routing chosen by a layout rule |
| `user` | given explicitly in the case configuration (e.g. camera poses) |

## Class hierarchy and external mappings

| Class | Parent | Brick | ASHRAE 223P |
|---|---|---|---|
| `Space` |  | brick:Space | s223:PhysicalSpace |
| `Zone` | `Space` | brick:HVAC_Zone | s223:DomainSpace |
| `Plenum` | `Space` | brick:Space | s223:PhysicalSpace |
| `Outdoors` | `Space` | brick:Outside | s223:PhysicalSpace |
| `Surface` |  |  |  |
| `Opening` | `Surface` |  |  |
| `SimComponent` |  | brick:Equipment | s223:Equipment |
| `AirTerminalUnit` | `SimComponent` | brick:Terminal_Unit | s223:TerminalUnit |
| `VAVTerminalUnit` | `AirTerminalUnit` | brick:VAV | s223:SingleDuctTerminal |
| `FanCoilUnit` | `AirTerminalUnit` | brick:FCU | s223:FanCoilUnit |
| `PackagedTerminalUnit` | `AirTerminalUnit` | brick:Terminal_Unit | s223:TerminalUnit |
| `Fan` | `SimComponent` | brick:Fan | s223:Fan |
| `CoolingCoil` | `SimComponent` | brick:Cooling_Coil | s223:CoolingCoil |
| `DXCoolingCoil` | `CoolingCoil` | brick:Cooling_Coil | s223:CoolingCoil |
| `HeatingCoil` | `SimComponent` | brick:Heating_Coil | s223:HeatingCoil |
| `HotWaterCoil` | `HeatingCoil` | brick:Hot_Water_Coil | s223:HeatingCoil |
| `OutdoorAirMixer` | `SimComponent` | brick:Economizer | s223:Equipment |
| `AirSplitter` | `SimComponent` |  | s223:Junction |
| `AirMixer` | `SimComponent` |  | s223:Junction |
| `ReturnPlenumComponent` | `SimComponent` |  | s223:Junction |
| `Chiller` | `SimComponent` | brick:Chiller | s223:Chiller |
| `Boiler` | `SimComponent` | brick:Boiler | s223:Boiler |
| `Pump` | `SimComponent` | brick:Pump | s223:Pump |
| `CoolingTower` | `SimComponent` | brick:Cooling_Tower | s223:CoolingTower |
| `UnitarySystem` | `SimComponent` | brick:RTU | s223:Equipment |
| `PlantPipe` | `SimComponent` |  | s223:Pipe |
| `OtherComponent` | `SimComponent` | brick:Equipment | s223:Equipment |
| `Element` |  |  |  |
| `Duct` | `Element` |  | s223:Duct |
| `Pipe` | `Element` |  | s223:Pipe |
| `Valve` | `Element` | brick:Valve | s223:Valve |
| `Diffuser` | `Element` | brick:Diffuser | s223:Diffuser |
| `ReturnGrille` | `Element` |  | s223:Equipment |
| `TerminalBox` | `Element` | brick:VAV | s223:SingleDuctTerminal |
| `CoilSection` | `Element` |  | s223:Equipment |
| `EquipmentCasing` | `Element` | brick:Equipment | s223:Equipment |
| `CondenserCoil` | `Element` | brick:Condenser | s223:Equipment |
| `CondenserFan` | `Element` | brick:Condenser_Fan | s223:Fan |
| `Motor` | `Element` | brick:Motor | s223:ElectricMotor |
| `Louver` | `Element` | brick:Damper | s223:Equipment |
| `RoofSurface` | `Element` | brick:Roof |  |
| `InsulationDefect` | `Element` |  |  |
| `Camera` |  | brick:Camera | s223:Sensor |

`DistrictCooling`, `DistrictHeating` and `WaterHeater:*` objects are classified as `Chiller` / `Boiler` (plant sources).

## Mapping to USD

| Ontology | USD |
|---|---|
| Space | `/World/<Space>` Xform; `semantic:class`; environment series `thermal:airTemperature`, `thermal:meanRadiantTemperature`, `thermal:reflectedTemperature`, `thermal:backgroundTemperature` |
| Surface / Opening | `/World/<Space>/Surfaces/<name>` Mesh; `ts:simSurface`, `ts:construction`, `ts:boundary`, `ts:face` (inside/outside), `thermal:emissivity`, `thermal:surfaceTemperature`; `ts:hostedBy`, `ts:locatedIn` |
| Element | `/World/<Space>/Elements/<name>` Mesh; `mep:medium`, `mep:size`, `mep:material`, `mep:streamNode`, `thermal:model`, `thermal:surfaceTemperature`, `mep:fluidTemperature`, `mep:fluidMassFlow`; `ts:represents`, `ts:connectedTo`, `ts:locatedIn` |
| SimComponent | `/World/Systems/<type_name>` Scope; `ts:simType`, `ts:role`, `ts:system`, `ts:ports` (`direction|medium|node|field`), `ts:size:*`; `ts:feeds` |
| Camera | `/World/Cameras/<name>` Camera; `thermal:resolution`, `thermal:band_um`, `thermal:camera:*`, `thermal:previewFrames`; `thermal:environment` -> Space |
| Fault | `ts:fault` on the affected element; envelope defects are Openings with `ts:surfaceType = Defect` |

Time-varying attributes are USD time samples (time code *i* = *i*-th EnergyPlus zone time step; labels in the layer
metadata `thermal:timeCodeLabels`) or, with `storage.time_series: sidecar`, arrays in `<scene>.series.npz` referenced by
the attribute's custom data `thermal:sidecarKey`.
