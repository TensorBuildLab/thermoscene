# Configuration reference

A case file is YAML, deep-merged over [`thermoscene/defaults.yaml`](../thermoscene/defaults.yaml). Relative paths are
resolved against the case file; `${ENERGYPLUS_DIR}` expands to the EnergyPlus installation (useful for its
`ExampleFiles` and `WeatherData`).

## model, output_dir, energyplus

```yaml
model: {idf: model.idf, epw: weather.epw}
output_dir: output
energyplus:
  executable: null              # else THERMOSCENE_ENERGYPLUS or `energyplus` on PATH
  timesteps_per_hour: 4         # replaces the model's Timestep
  run_periods:                  # replaces the model's RunPeriods; weather-file runs are switched on,
    - {name: SUMMER, start: "07-15", end: "07-16", start_day_of_week: Wednesday}   # design-day runs off
```

Without `run_periods` the model's own run periods are used. Output requests and `Output:SQLite` are always added.
Runs are cached in `energyplus.cache_dir` (default `<case dir>/.sim_cache`) under a hash of the prepared model, the
weather file and the path of the EnergyPlus executable, so scenes of several zones of one building share one simulation.
The EnergyPlus version is recorded in the stage (`ts:source`) and in `summary.json`.

## scene

| key | values | meaning |
|---|---|---|
| `zone` | EnergyPlus zone name | the space to build (required) |
| `plenum` | `auto` / `none` / zone name | `auto`: the zone on the other side of an interzone ceiling that is an `AirLoopHVAC:Return/SupplyPlenum` zone or named "*plenum*" |
| `roof.source` | `none` / `model` / `box` | where rooftop equipment goes. `model` uses the Roof surfaces in the scene (exterior skins); `box` needs `elevation` and `extent: [xmin, xmax, ymin, ymax]` and generates a roof deck |
| `roof.anchor` | `auto` / `[x, y]` | the shaft position under the AHU discharge; `auto` = the plenum service edge |
| `exterior_skins` | bool | outdoor-facing surfaces also get an exterior face with the outside-face temperature |
| `interzone_offset_m` | m | coincident interzone faces are pulled apart (avoids z-fighting) |

`equipment_location` (top level) overrides where plant sources go, e.g.
`equipment_location: {"Chiller:water": roof, Boiler: roof}`. Defaults: air-cooled chillers on the roof, water-cooled
chillers and boilers in a plant room (piped from a riser, not modelled). Only the sources of the AHU's water coils are
placed; cooling towers and the sources of reheat coils are traced but not generated.

## faults

```yaml
faults:
  # simulated by EnergyPlus: an opaque sub-surface (Door) whose construction is the host construction with its
  # highest-resistance layer replaced by a cavity. rect = [u0, u1, v0, v1] in metres from the host's lower-left
  # corner seen from outside (u along the surface, v up).
  # mode (cavity | remove) and cavity_resistance (m2K/W) default to fault_defaults.envelope_defect in defaults.yaml.
  # A cavity that would be the outermost or innermost layer is written as Material:NoMass (EnergyPlus rejects an
  # exposed Material:AirGap).
  - {type: envelope_defect, surface: South_Wall, rect: [4.0, 4.8, 0.3, 1.1], name: Insulation_Void,
     mode: cavity, cavity_resistance: 0.17}
  # generated element fault: an unwrapped segment of the first diffuser drop
  - {type: duct_insulation_gap, on: discharge, position: 0.4, length_m: 0.18}
  # any EnergyPlus fault object, passed through verbatim
  - {type: energyplus_object, idf: "FaultModel:Fouling:AirFilter, ..."}
```

## cameras

```yaml
cameras:
  - {name: WallCamera, space: zone, eye: [0.6, 3.5, 1.6], target: [3.2, 0.0, 1.0], hfov_deg: 62,
     frames: ["WINTER 07:00", "SUMMER 15:00"]}
  - {name: PlenumCamera, space: plenum, hfov_deg: 75,
     auto: {target: TerminalBox, height: 0.72, min_distance: 1.5, max_fraction: 0.15}}
  - {name: RooftopCamera, space: outdoors, auto: {target: CondenserCoil, distance: 6.5, height: 3.0}}
```

* `space`: `zone`, `plenum`, `outdoors` or a space id. It decides the reflected/background environment.
* `auto.target`: an element class (`Diffuser`, `TerminalBox`, `CondenserCoil`, ...), a surface type (`Window`, `Defect`,
  `Roof`), or an id. Indoors, candidates lie on a grid inside the zone footprint at `height` above the space floor;
  outdoors, on a ring of radius `distance` at `height` above the target. The best candidate maximises the visible share
  of the target (capped at `max_fraction`) plus a small bonus for the number of distinct objects in view.
* `frames`: exact labels (`"WINTER 01-15 07:30"`), or `"ENV HH:MM"` = that time on the last day of run period ENV.

## storage

`storage.time_series: timesamples` writes USD time samples (best for days to weeks; animated viewport colours).
`sidecar` writes `<scene>.series.npz` and references it from each attribute (`thermal:sidecarKey`), for long runs.

## Outputs

`output_dir` receives `<name>.usda` (and `<name>.series.npz` in sidecar mode), `scene.ttl` (RDF), `summary.json`
(serving chain, elements, provenance counts, build time) and `prepared.idf`. `--render` / `thermoscene render` add,
per camera, `renders/<camera>/segmentation.npy`, `<frame>_true.npy` (label), `<frame>_apparent.npy` (camera reading),
`provenance_map.npy` (index into simulated, physics, engineering_rule, heuristic, assumed_layout, user, background),
`provenance_share.json` and a preview PNG. `thermoscene web` writes a self-contained real-time viewer (HTML).

## Assumptions (defaults.yaml)

`sizing` (engineering rules), `layout` (layout rules), `materials` and `material_rules` (emissivity / solar absorptance
by element class and location), `thermal` (film coefficients, insulation, stagnation time constants, heuristic model
parameters), `camera` (resolution, field of view, camera emissivity/reflected-temperature settings, NETD, blur).
Calibrating the framework against measurements means editing this file.

## Extending

* **Thermal model** — add a function to `thermoscene/thermal/models.py` with `@model("name")`; it receives the element,
  its fluid temperature series and a context (results, environment, ambient helpers) and returns
  `(temperature series, provenance level, note)`. Select it with `element.thermal_model = "name"`.
* **Generator** — write a function `generate(gen, ...)` that adds `Element`s through `gen.element(...)`; `gen` exposes the
  traced chain (`gen.comps`, `gen.graph.has_role`, `gen.graph.plant_sources`), autosized values (`gen.size`), sizing
  rules and the scene. Register it in `pipeline.build`.
* **Component class** — extend `CLASS_MAP` in `energyplus/topology.py` and `CLASSES` in `ontology.py`.
