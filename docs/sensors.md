# Camera presets and realism parameters: values and sources

The presets in `thermoscene/defaults.yaml` (`sensors`) and the realism parameters (`realism`) are used by
`thermoscene.embodied`. Values marked *datasheet* were checked in the documents named; values marked *assumed* are not
published as numbers and are randomised per camera unit (`sensors.randomize`).

## Presets

| Parameter | lepton35 | handheld_320 | drone_640 | Status |
|---|---|---|---|---|
| Camera represented | Teledyne FLIR Lepton 3.5 | FLIR E8-XT | DJI Mavic 3T | |
| Pixels | 160 x 120 | 320 x 240 | 640 x 512 | datasheet |
| Horizontal field of view | 57 deg | 45 deg (45 x 34) | 49.4 deg (from 61 deg diagonal at 5:4) | datasheet (drone: derived) |
| Pixel pitch, f-number | 12 um, f/1.1 | (17 um assumed), f/1.5 | 12 um, f/1.0 | datasheet |
| NETD | < 50 mK | < 50 mK | <= 50 mK | datasheet |
| Accuracy | +-5 degC or 5 % (high gain) | +-2 degC or +-2 % | +-2 degC or +-2 % (high gain) | datasheet |
| Radiometric output step | 0.01 K (TLinear) | 0.01 K | 0.01 K | datasheet (Lepton); assumed |
| Flat-field correction | every 3 min | every 3 min | every 5 min | datasheet (Lepton; Boson+ for the drone class); assumed |
| Thermal time constant | 12 ms | 10 ms | 8 ms | datasheet (Lepton; Boson+); assumed |
| Lens distortion | k1 = -0.08 (about -12 % at the corner; datasheet < 13 %) | -0.02 | -0.03 | datasheet bound; assumed |
| Defective pixels | 0.005 % (typically one) | 0.02 % | 0.02 % | datasheet (Lepton); assumed |
| Residual offset non-uniformity | 40 mK | 30 mK | 30 mK | assumed (about 0.6-0.8 x NETD) |
| Column / row offsets | 30 / 15 mK | 20 / 10 mK | 20 / 10 mK | assumed |
| Radial shading (centre to corner) | 0.4 K | 0.3 K | 0.3 K | assumed |
| Offset drift between corrections | 0.15 K/min | 0.10 K/min | 0.10 K/min | assumed |

Sources: Teledyne FLIR Lepton Series datasheet (rev. 12/2025) and Lepton Engineering Datasheet (rev. 200: distortion,
frame rate, FFC defaults, accuracy, TLinear, time constant); Lepton with Radiometry datasheet (defective pixels);
Teledyne FLIR Boson+ Product Datasheet (release 117, 2026: time constant, FFC period, defect limits, distortion by
lens); FLIR E8-XT specification page; DJI Mavic 3T specification page. Characterisation papers that support the
assumed magnitudes and the structure of the model: Budzier and Gerlach (2015, J. Sens. Sens. Syst. 4, 187-197:
defective pixels, about +-1 K calibrated accuracy at steady ambient), Wolf et al. (2016, Sensors 16, 1121: residual
non-uniformity < 0.25 degC after drift modelling), Kelly et al. (2019, Remote Sens. 11, 567: drift and vignetting in
UAV flight), Aragon et al. (2020, Sensors 20, 3316: per-pixel calibration of vignetting and bias), Tempelhahn et al.
(2016, J. Sens. Sens. Syst. 5, 9-16: shutter-based correction), Oswald-Tranta (2018, J. Sens. Sens. Syst. 7, 13-20:
time constant and motion blur), Chrzanowski (2025, Metrol. Meas. Syst. 32: 3D noise components).

## Scene realism

| Parameter | Value | Status and source |
|---|---|---|
| Directional emissivity | eps_n [1 - (1 - cos theta)^5] (Schlick) | assumed; near-constant below 60 deg for non-metals (Barreira et al. 2021, Sensors 21, 1961); Schlick is common in renderers but approximate in the IR (ter Heerdt et al. 2026, J. Imaging 12, 33) |
| Specular share | foil 0.6, galvanised 0.5, aluminium jacket 0.7, glass 0.9, paints and plaster 0.05 | assumed (no LWIR BRDF data found for these facings) |
| Emissivities | foil-faced wrap 0.30 (clean foil 0.04, aged foil 0.2 design value), galvanised 0.25, aluminium jacket 0.12, glass 0.84, textiles 0.95, skin 0.98 | FLIR Ex manual table; Optris tables; Knauf design value; the wrap value is a dusty-facing assumption |
| Within-surface variation | temperature 0.15 K, emissivity 0.02 (x3 on foil, x2.5 galvanised, x2 aluminium), correlation length 0.25 m | assumed |
| Occupant clothing | ISO 7730 clothing-surface temperature with simulated air, mean radiant temperature and activity; ASHRAE 55 dynamic clothing (Schiavon and Lee 2013) | standard equations |
| Exposed skin | 35.7 - 0.028 M (bare-skin limit of the ISO 7730 equation; 33.7 degC at 1.2 met) | derived from ISO 7730 |
| Luminaire lens rise | +8 K at full lighting power | assumed (LED tube surfaces reach 30-40 degC: Siikanen et al. 2011) |
| Monitor rise | +8 K at full plug-load power | assumed |

The name of the software, thermoscene, is unrelated to the ThermoScenes dataset of measured RGB-thermal facade images
released with ThermoNeRF (Hassan et al. 2025, Adv. Eng. Inform. 65, 103345).
