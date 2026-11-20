# Embodied interface: thermal scenes as environments for inspection agents

`thermoscene.embodied` turns a built scene (the OpenUSD stage written by `thermoscene build` or `thermoscene auto`)
into something a robot-learning or perception pipeline can use directly:

| Component | What it does |
|---|---|
| `ThermalWorld(usd)` | Loads the scene once: triangles, temperature series of every prim, emissivities, materials, provenance, occupant presence, ceiling jets, space environments. `observe(pose, t, camera)` returns a `Frame` from any pose at any simulated time step. |
| `ThermalCamera` | Thermal-camera model: presets for camera classes, random units for domain randomisation (see below). |
| `InspectionEnv` | Gymnasium-style environment (`reset`, `step`, `render`, `action_space`, `observation_space`) with two tasks, fault search and coverage. |
| `export_dataset` | COCO-style labelled dataset (16-bit radiometric images, per-pixel labels, instance masks, camera metadata, datasheet). |

```python
from thermoscene.embodied import ThermalWorld, ThermalCamera, Pose
w = ThermalWorld("case_studies/B_hvac_system/output/B_hvac_system.usda")
cam = ThermalCamera.preset("handheld_320", seed=3)          # one unit of a 320 x 240 inspection-camera class
pose = Pose.look_at(eye=[2.5, 2.0, 1.6], target=[2.5, 0.0, 1.2])
f = w.observe(pose, w.time_index("WINTER 07:00"), cam)
f.reading, f.true, f.instance, f.category, f.provenance, f.emissivity, f.depth, f.fault, f.mixed
```

## Frames and labels

Every observation returns, per output pixel: the camera reading (deg C), the modelled true surface temperature, the
instance (scene prim) and semantic category, the provenance level of the true temperature at that time step (jets and
stagnant fluid lower it, as in the reference renderer), the effective emissivity, the depth along the optical axis,
a fault mask and a mixed-pixel mask (the pixel's sub-samples belong to more than one prim). The labels are the
modelled temperatures; the within-surface variation that the realism layer adds is in the reading only, as
unmodelled variation is in a field measurement.

## Rendering

Visibility is computed by a CPU z-buffer rasteriser at pixel centres with perspective-correct depth and near-plane
clipping. It reproduces the reference ray caster (`thermoscene.render.raycast`) pixel for pixel (agreement 99.99-100 %
on all cameras of cases B and C; temperatures within 1e-5 K) at 0.02-0.3 s per 320 x 240 view instead of 0.7-2.9 s.
Specular bounces are traced with a multi-origin ray caster. With `ThermalCamera.preset("reference")` and realism off,
an observation is identical to the reference renderer (tested in `tests/test_embodied.py`).

## Camera model (`defaults.yaml`, `sensors`)

In order: lens distortion (Brown radial) and pixel integration over sub-samples; optics blur (Gaussian approximation
of the diffraction-limited Airy core, sigma = 0.42 lambda N / pixel pitch, combined with lens aberration); in
radiance: per-pixel gain and offset non-uniformity remaining after correction, column and row offsets, radial shading,
offset drift that grows between flat-field (shutter) corrections and resets at each, temporal noise with the NETD
at 30 degC; radiometric inversion with the camera's emissivity and reflected-temperature settings; calibration error
within the datasheet accuracy; dead pixels; quantisation of the radiometric output; optional 8-bit plateau-equalised
output (AGC). Random fields (non-uniformity, stripes, dead pixels, calibration error) are drawn once per camera unit,
temporal noise per frame. Presets: `reference`, `ideal`, `lepton35`, `handheld_320`, `drone_640`;
`ThermalCamera.randomized(rng)` draws a unit with parameters scaled within the ranges in `sensors.randomize`.

## Scene realism (`defaults.yaml`, `realism`)

Directional emissivity (eps(theta) = eps_n (1 - (1 - cos theta)^5)), one specular bounce on materials with a
specular share (foil-faced duct wrap, bare and galvanised metal, aluminium jackets, glass), within-surface emissivity
and temperature variation (world-space value noise: the same surface looks the same from every pose), LWIR path
extinction. Occupants, luminaires, desks and monitors are generated from the energy model's internal gains during the
build (`internal_gains` in `defaults.yaml`; `thermoscene/generators/internal_gains.py`): occupants come and go with
the simulated occupant count, their clothing temperature follows ISO 7730 with the simulated air and radiant
temperatures and activity, luminaires and monitors follow the simulated lighting and plug-load power.

## Environment

```python
from thermoscene.embodied.env import InspectionEnv, ACTIONS
env = InspectionEnv(w, camera="lepton35", space="Plenum", task="fault_search", max_steps=200)
obs, info = env.reset(seed=0)                      # random start pose, time step and camera unit
obs, reward, terminated, truncated, info = env.step(ACTIONS.index("forward"))
```

Discrete actions: forward, back, strafe left/right (0.5 m), turn left/right (30 deg), tilt up/down (15 deg), wait
(advance simulated time by one EnergyPlus step), declare (the target is in the central window). Continuous actions:
`action_mode="continuous"`, Box(7). Observation: reading, pose, time features; `privileged=True` adds the labels.
The info dict always holds the labels, provenance and fault mask. Moves that leave the space or pass through
geometry are refused (`info["collided"]`). With `gymnasium` installed the class is a `gymnasium.Env`; otherwise
stand-in spaces with the same interface are used. Typical step time on one laptop core: 0.1-0.3 s with
`supersample=1`, about 1 s with the presets' default super-sampling.

## Dataset export

```bash
python -m thermoscene dataset case_studies/C_doe_medium_office/output/C_doe_medium_office.usda -o data/C --views 500
```

See the docstring of `thermoscene/embodied/dataset.py` for the layout: 16-bit centikelvin images (radiometric
"TLinear" convention), 8-bit AGC images, per-pixel label images, COCO-style `annotations.json` (RLE masks, boxes,
categories with super-categories envelope / hvac / internal_gain / fault, mean true temperature and reading per
instance, intrinsics, extrinsics, camera unit), and a `datasheet.md`.

## Isaac Sim

The scenes are OpenUSD stages with collision meshes; `thermoscene/isaac/thermal_camera.py` renders segmentation and
depth with Replicator and applies the same shading, hiding occupants that are absent at a time step. That script has
not been executed on GPU hardware (see `CLAUDE.md`).
