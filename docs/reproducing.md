# Reproducing the paper

All numbers, tables and figures of the paper are produced by scripts in this repository. Tested with EnergyPlus 22.2
and Python 3.12 on Windows 11 (an earlier run with EnergyPlus 25.2 on Linux gave the same element counts, topology
survey, zone sweep and sensitivity table; a few morning-reheat temperatures of case B differed by up to 1.3 K). No GPU
is needed (Isaac Sim is optional). `scripts/setup.sh` / `scripts/setup.ps1 -Evaluations` run the main steps below.
On Windows set `PYTHONUTF8=1`. Run the builds one after another, not in parallel.

```bash
pip install -e .                                                   # or: pip install -r requirements.txt
export THERMOSCENE_ENERGYPLUS=/path/to/EnergyPlus/energyplus      # e.g. C:/EnergyPlusV22-2-0/energyplus.exe

# case studies (B and C are the paper's case studies; A is a packaged-unit variant of B's office)
python -m thermoscene build case_studies/A_single_room/case.yaml --render
python -m thermoscene build case_studies/B_hvac_system/case.yaml --render
python -m thermoscene build case_studies/C_doe_medium_office/case.yaml --render

# evaluations (E1-E7 of the paper)
python case_studies/zone_sweep.py          # E3 -> docs/zone_sweep.md                 (Table 6)
python case_studies/topology_survey.py     # E4 -> docs/topology_survey.md            (Table 7)
python case_studies/morris.py              # E6 -> docs/morris.md                     (Table 8, ~7 min)
python case_studies/sensitivity.py         # E6 -> docs/sensitivity.md                (one-at-a-time, Supplementary)
python case_studies/benchmark.py           # Section 5.6 -> docs/benchmark.md          (element benchmarks, 2-D void)
python case_studies/annual.py              # E7 -> docs/annual.md                     (Table 9, two TMY3 years, ~5 min)
python case_studies/paper_numbers.py       # every number in Sections 5.1-5.7 -> docs/paper_numbers.md
python case_studies/paper_figures.py       # Figs. 1-8 and the graphical abstract -> docs/paper/figures/
python -m pytest -q tests                  # renderer agreement, physical sanity, topology, radiometry
python -m thermoscene web case_studies/B_hvac_system/output/B_hvac_system.usda -o docs/viewer/case_B.html
python -m thermoscene web case_studies/C_doe_medium_office/output/C_doe_medium_office.usda -o docs/viewer/case_C.html

# check a rerun against the committed numbers (what CI does)
python case_studies/compare_numbers.py case_studies/paper_numbers.json new_paper_numbers.json
```

| Paper item | Source |
|---|---|
| Fig. 1, Fig. 2 | `paper_figures.py` (`fig_pipeline`, `fig_ontology`) |
| Fig. 3 | traced chain of case B (`/World/Systems` in the USD), `fig_topology` |
| Fig. 4 | shaded ray-cast renders of the case B and C stages, `fig_geometry` |
| Fig. 5, Fig. 7 | `renders/<camera>/*_apparent.npy` and the per-frame `*_provenance.npy` of cases B and C |
| Fig. 6 | time series in the case B stage |
| Fig. 8 | `renders/<camera>/provenance_share.json` (mean over frames) |
| Graphical abstract | `paper_figures.py fig_graphical_abstract` |
| Table 5 | `case_studies/B_hvac_system/output/summary.json` |
| Table 6 | `docs/zone_sweep.md`, `case_studies/zone_sweep.json` |
| Table 7 | `docs/topology_survey.md` (traced vs declared chains, precision / recall) |
| Table 8 | `docs/morris.md`, `case_studies/morris.json` |
| Table 9 | `docs/annual.md`, `case_studies/annual.json` |
| Benchmarks (Section 5.6) | `docs/benchmark.md`, `case_studies/benchmark.json` |
| Numbers in the text of Sections 5.1–5.7 | `docs/paper_numbers.md`, `case_studies/paper_numbers.json` |
| Supplementary Material | `paper/build_supplementary.py` (assembles the `docs/*.md` tables) |
| Interactive environment (Section 4.1) | `docs/viewer/case_B.html`, `docs/viewer/case_C.html` |

Outputs are written to `case_studies/<case>/output/` (ignored by git), and the annual and Morris runs to
`case_studies/<case>/annual/` and `morris/`. EnergyPlus runs are cached in `case_studies/<case>/.sim_cache/`; delete it
to force a new simulation. The evaluation JSON files, the markdown tables in `docs/`, the figures and the viewers are
committed so that the paper's numbers can be checked without rerunning.

## Continuous integration

`.github/workflows/ci.yml` runs the unit tests and `ruff` on Python 3.10 and 3.12, and a second job that downloads
EnergyPlus 22.2 for Ubuntu, builds the three cases, runs the full tests and the topology survey, and compares the
regenerated paper numbers with the committed ones (absolute tolerance 0.15, one rounding step of the 0.1-rounded
values, since Linux and Windows builds of EnergyPlus can differ in the last digits; run times and file sizes are not
compared).
