# Topology survey

The generic tracer (`thermoscene.energyplus.topology`) applied, without any model-specific configuration, to EnergyPlus example models. **180 of 180 conditioned zones traced.** *Traced zones*: conditioned zones for which a non-empty serving chain was recovered. Numbers in parentheses: how many traced zones contain that class.

| Model | Zones (conditioned) | HVAC components | Traced zones | Mean chain length | Component classes found (all zones) |
|---|---|---|---|---|---|
| 5ZoneAirCooled | 6 (5) | 32 | 5/5 | 20.0 | VAVTerminalUnit (5), Fan (5), CoolingCoil (5), HotWaterCoil (5), OutdoorAirMixer (5), Chiller (5), Boiler (5), Pump (5) |
| RefBldgMediumOfficeNew2004_Chicago | 18 (15) | 64 | 15/15 | 10.0 | VAVTerminalUnit (15), Fan (15), DXCoolingCoil (15), HeatingCoil (15), OutdoorAirMixer (15) |
| RefBldgLargeOfficeNew2004_Chicago | 19 (16) | 91 | 16/16 | 24.9 | VAVTerminalUnit (16), Fan (16), CoolingCoil (16), HotWaterCoil (16), OutdoorAirMixer (16), Chiller (16), Boiler (16), Pump (16), CoolingTower (16) |
| RefBldgSmallOfficeNew2004_Chicago | 6 (5) | 48 | 5/5 | 8.0 | AirTerminalUnit (5), Fan (5), DXCoolingCoil (5), HeatingCoil (5), OutdoorAirMixer (5) |
| RefBldgOutPatientNew2004_Chicago | 118 (86) | 222 | 86/86 | 14.5 | VAVTerminalUnit (86), Fan (86), DXCoolingCoil (86), HeatingCoil (41), HotWaterCoil (86), OutdoorAirMixer (86), Boiler (86), Pump (86) |
| RefBldgMidriseApartmentNew2004_Chicago | 27 (27) | 231 | 27/27 | 7.4 | AirTerminalUnit (27), Fan (27), DXCoolingCoil (24), HeatingCoil (27), OutdoorAirMixer (24) |
| FanCoilAutoSize | 3 (3) | 29 | 3/3 | 15.0 | FanCoilUnit (3), Fan (3), CoolingCoil (3), HotWaterCoil (3), OutdoorAirMixer (3), Chiller (3), Boiler (3), Pump (3) |
| PackagedTerminalHeatPump | 3 (3) | 18 | 3/3 | 6.0 | PackagedTerminalUnit (3), Fan (3), DXCoolingCoil (3), HeatingCoil (3), OutdoorAirMixer (3) |
| HVACTemplate-5ZonePackagedVAV | 6 (5) | 24 | 5/5 | 9.0 | VAVTerminalUnit (5), Fan (5), DXCoolingCoil (5), HeatingCoil (5), OutdoorAirMixer (5) |
| HVACTemplate-5ZoneVAVWaterCooled | 6 (5) | 37 | 5/5 | 23.0 | VAVTerminalUnit (5), Fan (5), CoolingCoil (5), HotWaterCoil (5), OutdoorAirMixer (5), Chiller (5), Boiler (5), Pump (5), CoolingTower (5) |
| HVACTemplate-5ZoneFanCoil | 6 (5) | 46 | 5/5 | 20.0 | FanCoilUnit (5), Fan (5), CoolingCoil (5), HotWaterCoil (5), OutdoorAirMixer (5), Chiller (5), Boiler (5), Pump (5), CoolingTower (5) |
| HVACTemplate-5ZonePTAC | 6 (5) | 32 | 5/5 | 7.0 | PackagedTerminalUnit (5), Fan (5), DXCoolingCoil (5), HeatingCoil (3), HotWaterCoil (2), OutdoorAirMixer (5), Boiler (2), Pump (2) |
