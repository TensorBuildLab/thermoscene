# CLAUDE.md — thermoscene

Project memory for Claude Code. Read this before changing anything.

## What this is

`thermoscene` turns an ordinary EnergyPlus model into a 3D thermal scene (OpenUSD) of one zone, its envelope and the
HVAC system serving it, as a virtual training environment for the thermal perception of ground robots and drones.
It accompanies a journal paper (target: Energy and Buildings / Building and Environment / Building Simulation /
Automation in Construction):

*Virtual thermal training environments for robots from building energy models: an ontology-driven pipeline for
buildings and HVAC systems* — Liang Zhang (University of Arizona), Huanrui Yang (University of Arizona),
Yiyuan Jia (Big Ladder Software).

The manuscript is drafted outside this repo (exported Word/Markdown goes in `paper/` if added). Its figures are
generated here: `docs/paper/figures/`.

## Setup

```bash
bash scripts/setup.sh            # venv + deps, finds EnergyPlus, builds A/B/C, runs tests, git init (Windows: scripts/setup.ps1)
# or by hand:
pip install -r requirements.txt
export THERMOSCENE_ENERGYPLUS=/path/to/EnergyPlus-25.2/energyplus     # tested with EnergyPlus 25.2
```

## Commands

```bash
python -m thermoscene inspect <model.idf> [--zone Z]       # zones, plenum, traced HVAC chain (no build)
python -m thermoscene build case_studies/B_hvac_system/case.yaml --render
python -m thermoscene render <scene.usda>                  # reference CPU renderer (renders/<camera>/...)
python -m thermoscene web <scene.usda> -o viewer.html      # self-contained real-time browser viewer (see docs/viewer/)
python -m pytest -q tests                                  # must pass before committing
python case_studies/topology_survey.py                     # paper Table 7 -> docs/topology_survey.md
python case_studies/zone_sweep.py                          # paper Table 6 -> docs/zone_sweep.md
python case_studies/sensitivity.py                         # paper Table 8 -> docs/sensitivity.md
python case_studies/paper_figures.py [fig_name ...]        # paper Figs 1-8 -> docs/paper/figures/
```

Case outputs (`case_studies/*/output`, `sweep`, `sensitivity`, `.sim_cache`) are git-ignored and must be rebuilt
after a fresh clone (build A, B, C with `--render` before running the figure script or the full tests).

## Architecture (five stages, one ontology)

1. `energyplus/` — `idf_text` injects outputs/run periods/faults into a copy of the user model; `runner` simulates
   (cached by SHA-256 in `.sim_cache`); `model` reads epJSON geometry; `results` reads SQLite; `topology` builds the
   generic node graph and traces the chain serving a zone.
2. `scene/` — `spaces` (zone, auto-detected plenum, outdoors, exterior skins), `geometry`, `faults`.
3. `generators/` — `air_devices` (diffusers, grilles), `distribution` (plenum ducts, terminal box, reheat piping),
   `equipment` (AHU sections from the traced air loop, packaged condenser, roof plant, pipes, pumps, valves).
4. `thermal/` — `environment` (sun, sky, space environments), `models` (registry of surface-temperature models),
   `radiometry` (8–14 µm band).
5. `usd/writer.py`, `render/` (`shading` shared by all renderers, `raycast` reference renderer + provenance maps,
   `cameras` explicit/automatic), `isaac/thermal_camera.py`, `web/` (exporter + `viewer.html`).

`ontology.py` defines Space, Surface, Opening, SimComponent, Element, Port, Stream, Camera, Fault and the six
provenance levels; see `docs/ontology.md`.

## Rules for changes

- **No model-specific names.** The topology tracer and generators must never look up EnergyPlus objects or nodes by
  name; derive everything from field semantics, loop/branch membership and ports. Re-run the topology survey after
  changing `topology.py` (expected: 180/180 conditioned zones traced).
- **Every non-simulated quantity gets a provenance level** (`Thing.prov(...)` / `scene.set_series(..., level, note)`).
- **All parameters live in `thermoscene/defaults.yaml`.** No magic numbers in code.
- **One radiometry, three renderers.** `render/shading.py` (Python reference, Isaac) and `web/viewer.html` (JS + GLSL)
  must produce identical numbers. If you change radiometry, jets or camera settings, change both and keep
  `tests/test_core.py` passing; the web viewer was checked against Python to 1e-4 K via Node.
- **Figures:** `paper_figures.py` uses Liberation Sans; schematic boxes are sized from measured text (`box_size`,
  `draw_box`); keep figure 1–3 content unchanged unless the paper changes.
- Keep paper numbers consistent with `docs/*.md`, `case_studies/zone_sweep.json`, `case_studies/sensitivity.json`.

## Status and open items

- Repository verified after a rebuild (2026-10-30): all three cases reproduce the paper's counts (A 8/12/8, B 14/28/17,
  C 26/37/10), topology survey 180/180, zone sweep 15/15, sensitivity table identical to paper Table 8, 6/6 tests pass.

- Isaac Sim script: Replicator calls written against the 4.5–6.x API, **never executed on a GPU**. The shading path it
  feeds is tested. First job on a GPU machine: run it on case B and compare with `renders/` (the script prints the
  segmentation agreement).
- Paper: references were compiled from memory — verify authors/volumes/DOIs; two 2026 arXiv entries (HEARTH,
  ADAPT) lack author lists. Confirm Big Ladder Software's city; designate the corresponding author; CRediT,
  competing-interest and acknowledgement sections are placeholders.
- Scope (stated in the paper): scenes are replay-only. Next steps toward a robot test environment: (1) live thermal
  sensor on a moving robot/drone in Isaac Sim with time interpolation; (2) navigable multi-room floors, doors, stairs,
  plenum/roof access, RGB materials, task definitions and metrics, fault randomisation; (3) interaction via EnergyPlus
  runtime API co-simulation or a learned surrogate; (4) field thermography for sim-to-real validation.
- Known limits: rule-based layout (one plenum, orthogonal routes, no clash avoidance); uncalibrated heuristics
  (casings, motors, condensers, diffuser faces, ceiling jets); one temperature per EnergyPlus surface.

## Lessons learned (do not rediscover)

- DOE reference models set `SimulationControl` to skip weather-file runs; `runner.prepare` switches them on when
  run periods are configured.
- Models may request the same output hourly; `Results.series` prefers the time-step frequency row.
- Keep only zone time steps in the time axis (HVAC sub-steps appear in SQLite).
- DX coils inside `CoilSystem:Cooling:DX` inherit their container's air-loop membership.
- Auto-camera `height` is relative to the space floor (top-floor zones sit at ~8 m).
- Single-floor hand-made cases (A, B) set `scene.exterior_skins: false` (no facade between that floor and the roof).
- `ComponentSizes` descriptions can carry trailing spaces; match on whitespace-normalised text.
- Ceiling pixels inside a diffuser's jet footprint are labelled heuristic in provenance maps.
- When scripting in `sh`, brace expansion (`mkdir a/{b,c}`) does not work; use bash or explicit paths.
