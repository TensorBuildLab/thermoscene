# CLAUDE.md — thermoscene

Project memory for Claude Code. Read this before changing anything.

## What this is

`thermoscene` turns an ordinary EnergyPlus model into a 3D thermal scene (OpenUSD) of one zone, its envelope, the
HVAC system serving it and its occupants, and into an environment for embodied inspection agents (robots, drones,
crawlers): observation from any pose and simulated time through a realistic thermal-camera model with per-pixel
labels, a Gymnasium-style environment and a COCO dataset exporter (`thermoscene/embodied/`, `docs/embodied.md`).

Current paper (revision of 2026-11-19, target Automation in Construction, single author Liang Zhang, University of
Arizona and National Laboratory of the Rockies): *Building energy models as labelled thermal environments for embodied
inspection agents*. Its sources, figures and upload files are in `../../Automation in Construction Submission/`
(`_source/draft_v2_*.md`, `assemble_manuscript.py`, `make_figures.py`, `build_package.py`). Its figures come from
`case_studies/embodied_figures.py`; its numbers from `case_studies/corpus_survey.py` (RQ1), `measurement_gap.py` (RQ2),
`inspection_timing.py` and `annual.py` (RQ3) and `env_baselines.py` (agents). Earlier versions: Energy and Buildings
manuscript in `paper/` (`manuscript_EnB.*`, figures from `paper_figures.py`, numbers from `paper_numbers.py`) and the
Advanced Engineering Informatics package (desk-rejected 2026-11-18).

## Setup

```bash
bash scripts/setup.sh            # venv + deps, finds EnergyPlus, builds A/B/C, runs tests, git init (Windows: scripts/setup.ps1)
# or by hand:
pip install -r requirements.txt
export THERMOSCENE_ENERGYPLUS=/path/to/EnergyPlus/energyplus           # tested with EnergyPlus 22.2 (C:/EnergyPlusV22-2-0)
export PYTHONUTF8=1                                                       # Windows
```

## Commands

```bash
python -m thermoscene inspect <model.idf> [--zone Z]       # zones, plenum, traced HVAC chain (no build)
python -m thermoscene build case_studies/B_hvac_system/case.yaml --render
python -m thermoscene auto <model.idf> [--epw F] [--zone NAME|largest|all] [--out DIR] [--render]
                                                           # any model, no YAML: writes <out>/auto_case.yaml, then builds it
python -m thermoscene render <scene.usda>                  # reference CPU renderer (renders/<camera>/...)
python -m thermoscene web <scene.usda> -o viewer.html      # self-contained real-time browser viewer (see docs/viewer/)
python -m pytest -q tests                                  # must pass before committing
python case_studies/topology_survey.py                     # paper Table 7 -> docs/topology_survey.md
python case_studies/corpus_survey.py topology [--jobs 4]   # ALL ExampleFiles, no simulation (~11 min) -> corpus_survey.json
python case_studies/corpus_survey.py scenes [--jobs 3]     # auto mode + render on every example file (hours) -> corpus_scenes.json
python case_studies/corpus_survey.py report                # docs/corpus_survey.md from both JSON files
                                                           # (resumable: --redo all, --failed only failures, --only PATTERN)
python case_studies/zone_sweep.py                          # paper Table 6 -> docs/zone_sweep.md
python case_studies/morris.py                              # paper Table 8 -> docs/morris.md (~7 min)
python case_studies/sensitivity.py                         # one-at-a-time (Supplementary) -> docs/sensitivity.md
python case_studies/annual.py                              # paper Table 9 -> docs/annual.md (two TMY3 years, ~7 min)
python case_studies/benchmark.py                           # Section 5.6 -> docs/benchmark.md
python case_studies/paper_numbers.py                       # numbers in Sections 5.1-5.7 -> docs/paper_numbers.md
python case_studies/paper_figures.py [fig_name ...]        # paper Figs 1-8 + graphical abstract -> docs/paper/figures/
# embodied-inspection paper (2026-11 revision)
python case_studies/measurement_gap.py [A|B|C]             # RQ2: pixel gap, contrast fidelity, distance (~70 min) -> docs/measurement_gap.md
python case_studies/inspection_timing.py                   # RQ3: detectability over a TMY3 year, timing policies (~15 min)
python case_studies/env_baselines.py                       # agents in InspectionEnv (random / label-free / privileged)
python case_studies/embodied_figures.py [name ...]         # emb_* figures + graphical abstract -> docs/paper/figures/
python -m thermoscene dataset <scene.usda> -o out --views N # COCO dataset;  python -m thermoscene episode ... (demo)
```

Case outputs (`case_studies/*/output`, `sweep`, `sensitivity`, `morris`, `annual`, `.sim_cache`) are git-ignored and must be rebuilt
after a fresh clone (build A, B, C with `--render` before running the figure script or the full tests).

## Architecture (five stages, one ontology)

1. `energyplus/` — `idf_text` injects outputs/run periods/faults into a copy of the user model; `runner` simulates
   (cached by SHA-256 in `.sim_cache`); `model` reads epJSON geometry; `results` reads SQLite; `topology` builds the
   generic node graph and traces the chain serving a zone.
2. `scene/` — `spaces` (zone, auto-detected plenum, outdoors, exterior skins), `geometry`, `faults`.
3. `generators/` — `air_devices` (diffusers, grilles), `distribution` (plenum ducts, terminal box, reheat piping),
   `equipment` (AHU sections from the traced air loop, packaged condenser, roof plant, pipes, pumps, valves).
4. `thermal/` — `environment` (sun, sky, space environments), `models` (registry of surface-temperature models,
   including occupants (ISO 7730), luminaires and plug loads), `radiometry` (8–14 µm band).
   `generators/internal_gains` places workstations/occupants and luminaires from the simulated occupant count and
   lighting/plug power; occupants carry a `thermal:present` series honoured by all renderers.
5. `usd/writer.py`, `render/` (`shading` shared by all renderers, `raycast` reference renderer + provenance maps,
   `cameras` explicit/automatic), `isaac/thermal_camera.py`, `web/` (exporter + `viewer.html`).

`ontology.py` defines Space, Surface, Opening, SimComponent, Element, Port, Stream, Camera, Fault and the six
provenance levels; see `docs/ontology.md`.

## Rules for changes

- **No model-specific names.** The topology tracer and generators must never look up EnergyPlus objects or nodes by
  name; derive everything from field semantics, loop/branch membership and ports (the plenum is found from declared
  plenums or unconditioned zones excluded from floor area, never by name). Re-run the topology survey after changing
  `topology.py` (expected since 2026-11-19: 180/180 traced, precision 1.000, recall 1.000 = 2503/2503, 180/180 exact;
  it was recall 0.998, 174/180 exact before zone exhaust fans were linked to their zones) and the corpus topology
  stage (`corpus_survey.py topology`, numbers in `docs/corpus_survey.md`).
- **Every non-simulated quantity gets a provenance level** (`Thing.prov(...)` / `scene.set_series(..., level, note)`).
- **All parameters live in `thermoscene/defaults.yaml`.** No magic numbers in code.
- **One radiometry, three renderers.** `render/shading.py` (Python reference, Isaac) and `web/viewer.html` (JS + GLSL)
  must produce identical numbers. If you change radiometry, jets or camera settings, change both and keep
  `tests/test_core.py` passing. The viewer's lookup tables agree with Python to < 1e-3 K (1e-4 K at 10-70 °C); by
  design the viewer omits optical blur and lets each surface reflect its own space's MRT (the free camera has no space).
  The spectral band is global (`thermal.band_um`, read by `thermal/radiometry.py` and written to every camera).
- **Figures:** `paper_figures.py` uses Liberation Sans; schematic boxes are sized from measured text (`box_size`,
  `draw_box`); keep figure 1–3 content unchanged unless the paper changes.
- Keep paper numbers consistent with `docs/*.md`, `case_studies/zone_sweep.json`, `case_studies/sensitivity.json` and
  `case_studies/paper_numbers.json` (rerun `paper_numbers.py` after any change that can move a temperature).

## Status and open items

- 2026-11-19 (embodied revision): new `thermoscene/embodied/` (world + rasteriser that reproduces render.raycast
  pixel for pixel, sensor model with presets lepton35 / handheld_320 / drone_640 from datasheets in docs/sensors.md,
  scene realism, InspectionEnv, dataset export, contrast_series); internal gains; corpus survey and auto mode (merged
  from a helper worktree). Counts now A 8/25/8, B 14/41/17, C 26/131/10 (occupants, luminaires, desks added);
  42 tests pass. Results: corpus 4,088/4,088 zones traced (P 0.993, R 1.000), 677 of 685 in-scope models built and rendered (all that EnergyPlus 22.2 runs as shipped);
  cameras keep 87-99 % of envelope contrasts but 16-67 % on low-emissivity HVAC surfaces; the duct gap (true -6.45 K)
  reads about -1.0 K (no sign flip); detection 49 % any time -> 100 % model-informed for the duct gap (sigma from 64 camera units per view; 100 % also for the noisiest tenth of units, 16 % any time).
  Not committed yet (the user commits).

- Repository re-verified 2026-10-30 with EnergyPlus 22.2 on Windows 11 (laptop i7-1185G7): counts A 8/12/8,
  B 14/28/17, C 26/37/10; topology survey 180/180 (precision 1.000, recall 0.998, 174/180 exact); zone sweep 15/15;
  Morris screening of 15 parameters; annual runs of B (Phoenix) and C (Chicago); all tests pass (`test_core.py`,
  `test_plausibility.py`). Paper text numbers, tables, figures 4-8 and the graphical abstract were regenerated from
  this run. Timings were measured with other applications loading the CPU (zone sweep 2-7 s per scene).
- Stagnation time constants (air 45, water 120, valve 30 min) are rounded lumped-capacitance estimates
  (`benchmark.py`); the valve constant dominates the valve contrast after reheat (Morris mu* 11.9 K).
- Open review items (see the review folder, `07_changes_made.md`): an EnergyPlus FaultModel case (M3), HVAC energy
  penalty of the void from paired runs (M1), heuristic models vs. published data (M10), a source for every default
  (M11), jet renormalisation (M16), more system types for the tracer (M18), outdoor view factors (S3).
- The GitHub repository is public since 2026-11-06 (it was private before); a Zenodo DOI for the submitted version is
  still recommended. Authors must still decide: author list, acknowledgements/funding, competing-interest statement
  (Big Ladder), and re-check the Energy and Buildings Guide for Authors (not downloadable here).
- A retargeted submission package for Advanced Engineering Informatics (manuscript, figures as separate files,
  compliance report against that journal's Guide for Authors) is in `../../Advanced Engineering Informatics Submission/`
  (outside this repository; its `_source/` holds the Markdown sources and build scripts).
- Isaac Sim script: Replicator calls written against the 4.5–6.x API, **never executed on a GPU**. The shading path it
  feeds is tested. First job on a GPU machine: run it on case B and compare with `renders/` (the script prints the
  segmentation agreement).
- Paper: references were checked against Crossref/arXiv/OSTI/publisher records on 2026-10-29 (DOIs added; HEARTH is
  Su et al., ADAPT is Yang et al.). ASHRAE 223P was still a public-review draft then; recheck before submission.
  Confirm Big Ladder Software's city. Abstract 249 words, 6 keywords, about 9,000 words of main text (53 pages).
- Not yet in `defaults.yaml`: geometric detailing constants in the generators (terminal-box proportions, casing
  margins, fan positions, capacity fallbacks) and the auto-camera search grid and score weights.
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
- Windows: Liberation Sans is missing, so figures 1-3 regenerate with Arial and come out taller; keep the committed
  figures 1-3 unless their content changes (`paper_figures.py fig_geometry fig_timeseries fig_provenance_shares
  fig_thermal_cases` regenerates only 4-8).
- `np.load` of an NPZ decompresses on every key access; `Series` loads the sidecar once.
- EnergyPlus SQLite has no index on `ReportData(ReportDataDictionaryIndex)`; `Results` creates one (12 ms -> 0.4 ms per
  series read).
- `paper_numbers.py` quotes differences computed from the rounded values it prints, so "X K (a vs b)" adds up.
- Two builds started at the same moment on Windows once failed to find energyplus.exe; run builds one after another.
- Do not run timing-sensitive scripts (zone sweep, paper_numbers cost) while anything else runs; timings double.
- A failed EnergyPlus run must not leave an empty `eplusout.sql` in `.sim_cache` (it poisons later builds);
  `_cached_simulation` and `Results` refuse empty files.
- The sun comes from the EPW LOCATION header (Site:Location only without an EPW); for case C they differ by 0.2 deg.
- In this environment Bash heredocs collapse double backslashes: write Python helper scripts to a file instead.
- Object types written in fields are case-insensitive in EnergyPlus (`AirloopHVAC:UnitarySystem` in a Branch):
  `EPModel.objs/obj` and `topology._excluded` match case-insensitively, and `obj` is indexed (a linear scan per call
  made the reference walk of a 130-zone hospital take 6 minutes).
- Zone equipment acting without an air inlet node (baseboards, radiant panels, exhaust fans) is linked to its zone via
  the equipment list; declared equipment with no node fields (ERV, HX-assisted coil systems, electric baseboards) is a
  component with its branch's and its parts' ports. Do not add per-class special cases where a field rule exists.
- EnergyPlus may override the requested time step (Conduction Finite Difference forces 20 per hour); `Results` uses the
  interval of the 'Zone Timestep' rows. With one time step per hour the SQLite file has HVAC sub-steps only (auto mode
  raises the time step to at least 4 per hour).
- Windows: USD cannot write a file whose path exceeds 260 characters (`TfSafeOutputFile` rename fails); auto mode and
  the corpus survey keep scene names and folders short.
- Windows cannot delete an open SQLite file: close/garbage-collect `Results` before deleting a simulation cache, and a
  cache directory with `eplusout.sql` but no `eplusout.err` is incomplete (the pipeline re-simulates it).
- Several example files do not run in EnergyPlus 22.2 as shipped; the corpus survey re-runs the unmodified file
  (design days) to tell EnergyPlus's own failures from thermoscene's.
