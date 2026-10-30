# Reproducing the paper

All numbers, tables and figures of the paper are produced by scripts in this repository. Tested with EnergyPlus 25.2
and Python 3.12 on Linux; no GPU is needed (Isaac Sim is optional). `scripts/setup.sh` runs every step below.

```bash
pip install -r requirements.txt
export THERMOSCENE_ENERGYPLUS=/path/to/EnergyPlus-25.2/energyplus

# case studies (B and C are the paper's case studies; A is a packaged-unit variant of B's office)
python -m thermoscene build case_studies/A_single_room/case.yaml --render
python -m thermoscene build case_studies/B_hvac_system/case.yaml --render
python -m thermoscene build case_studies/C_doe_medium_office/case.yaml --render

# evaluations
python case_studies/topology_survey.py     # E4 -> docs/topology_survey.md        (Table 7)
python case_studies/zone_sweep.py          # E3 -> docs/zone_sweep.md             (Table 6)
python case_studies/sensitivity.py         # E6 -> docs/sensitivity.md            (Table 8)
python case_studies/paper_figures.py       # Figs. 1-8 -> docs/paper/figures/
python -m pytest -q tests                  # renderer agreement, physical sanity, topology
python -m thermoscene web case_studies/B_hvac_system/output/B_hvac_system.usda -o docs/viewer/case_B.html
```

| Paper item | Source |
|---|---|
| Fig. 1, Fig. 2 | `paper_figures.py` (`fig_pipeline`, `fig_ontology`) |
| Fig. 3 | traced chain of case B (`/World/Systems` in the USD), `fig_topology` |
| Fig. 4 | shaded ray-cast renders of the case B and C stages, `fig_geometry` |
| Fig. 5, Fig. 7 | `renders/<camera>/*_apparent.npy` and `provenance_map.npy` of cases B and C |
| Fig. 6 | time series in the case B stage |
| Fig. 8 | `renders/<camera>/provenance_share.json` |
| Table 5 | `case_studies/B_hvac_system/output/summary.json` |
| Table 6 | `docs/zone_sweep.md`, `case_studies/zone_sweep.json` |
| Table 7 | `docs/topology_survey.md` |
| Table 8 | `docs/sensitivity.md`, `case_studies/sensitivity.json` |
| Interactive environment (Section 4.1) | `docs/viewer/case_B.html` |

Outputs are written to `case_studies/<case>/output/` (ignored by git). EnergyPlus runs are cached in
`case_studies/<case>/.sim_cache/`; delete it to force a new simulation. The two evaluation JSON files, the markdown
tables in `docs/`, the figures and the viewers are committed so that the paper's numbers can be checked without rerunning.
