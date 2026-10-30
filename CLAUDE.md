# CLAUDE.md — thermoscene

Project memory for Claude Code. Read this before changing anything.

## What this is

`thermoscene` turns an ordinary EnergyPlus model into a 3D thermal scene (OpenUSD) of one zone, its envelope and the
HVAC system serving it: labelled, simulation-grounded thermography for building diagnostics, whether the camera is
handheld, on a drone or on a ground robot. It accompanies a journal paper (target: Energy and Buildings):

*From energy models to thermal scenes: an ontology-driven pipeline for physically grounded thermography of buildings
and HVAC systems* — Liang Zhang (University of Arizona), Huanrui Yang (University of Arizona), a student author (TBD),
Yiyuan Jia (Big Ladder Software). Reframed on 2026-10-30 after a mock review for Energy and Buildings (the review and
the item-by-item changes are in `../../reviews/2026-10-29_energy-and-buildings_thermoscene/`).

The manuscript is in `paper/`: `manuscript_EnB.docx` (current, Energy and Buildings format, real Word equations,
EndNote citations) built from `manuscript_EnB.md` + `references.ris` with the `manuscript-template` skill (see
`paper/README.md`); `manuscript.docx/.md` are the earlier author-year export. Figures are generated here:
`docs/paper/figures/` (figs 4-8 at 300 dpi, graphical abstract); every number in Sections 5.1-5.7 comes from
`case_studies/paper_numbers.py`. `paper/supplementary_EnB.*` is assembled from `docs/*.md` by
`paper/build_supplementary.py`; `paper/submission/` holds the upload files (line-numbered manuscript, highlights,
graphical abstract, cover letter).

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
python -m thermoscene render <scene.usda>                  # reference CPU renderer (renders/<camera>/...)
python -m thermoscene web <scene.usda> -o viewer.html      # self-contained real-time browser viewer (see docs/viewer/)
python -m pytest -q tests                                  # must pass before committing
python case_studies/topology_survey.py                     # paper Table 7 -> docs/topology_survey.md
python case_studies/zone_sweep.py                          # paper Table 6 -> docs/zone_sweep.md
python case_studies/morris.py                              # paper Table 8 -> docs/morris.md (~7 min)
python case_studies/sensitivity.py                         # one-at-a-time (Supplementary) -> docs/sensitivity.md
python case_studies/annual.py                              # paper Table 9 -> docs/annual.md (two TMY3 years, ~7 min)
python case_studies/benchmark.py                           # Section 5.6 -> docs/benchmark.md
python case_studies/paper_numbers.py                       # numbers in Sections 5.1-5.7 -> docs/paper_numbers.md
python case_studies/paper_figures.py [fig_name ...]        # paper Figs 1-8 + graphical abstract -> docs/paper/figures/
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
4. `thermal/` — `environment` (sun, sky, space environments), `models` (registry of surface-temperature models),
   `radiometry` (8–14 µm band).
5. `usd/writer.py`, `render/` (`shading` shared by all renderers, `raycast` reference renderer + provenance maps,
   `cameras` explicit/automatic), `isaac/thermal_camera.py`, `web/` (exporter + `viewer.html`).

`ontology.py` defines Space, Surface, Opening, SimComponent, Element, Port, Stream, Camera, Fault and the six
provenance levels; see `docs/ontology.md`.

## Rules for changes

- **No model-specific names.** The topology tracer and generators must never look up EnergyPlus objects or nodes by
  name; derive everything from field semantics, loop/branch membership and ports (the plenum is found from declared
  plenums or unconditioned zones excluded from floor area, never by name). Re-run the topology survey after changing
  `topology.py` (expected: 180/180 traced, precision 1.000, recall 0.998 = 2497/2503, 174/180 exact).
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
- Authors must still decide: author list, acknowledgements/funding, competing-interest statement (Big Ladder),
  public release + Zenodo DOI, and re-check the Energy and Buildings Guide for Authors (not downloadable here).
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
