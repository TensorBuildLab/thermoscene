# Manuscript

*From energy models to thermal scenes: an ontology-driven pipeline for physically grounded thermography of buildings
and HVAC systems* (prepared for Energy and Buildings).

| File | Content |
|---|---|
| **`manuscript_EnB.docx`** | **current version**, formatted for Energy and Buildings: Times New Roman 12 pt, double spacing, numbered headings, real Word equations numbered at the margin, three-line tables, Fig./Table captions with live cross-references, numbered references as EndNote fields |
| `manuscript_EnB.md` | its source (template Markdown: `[@key]` citations, `$$ … $$ {#eq:…}` equations, `@fig:`/`@tbl:`/`@sec:` cross-references) |
| `supplementary_EnB.docx`, `.md` | Supplementary Material: one-at-a-time sensitivity, benchmarks, annual fault statistics, zone sweep and per-model topology results; assembled from `docs/*.md` by `build_supplementary.py` |
| `cover_letter_EnB.md` | cover letter (bracketed items are for the authors to complete) |
| `references.ris` | the references with complete author lists and DOIs (cited keys come from here) |
| `submission/` | upload files: manuscript with continuous line numbers, highlights, graphical abstract |
| `manuscript.docx`, `manuscript.md` | an earlier export in author-year style (equations as LaTeX text), kept for reference |

The graphical abstract is `docs/paper/figures/graphical_abstract.png` (and `.pdf`), made by
`python case_studies/paper_figures.py fig_graphical_abstract` from the paper's own renders; no AI image tools were used.

Rebuild the formatted files after editing the Markdown (the `manuscript-template` Claude skill):

```bash
python paper/build_supplementary.py
python ~/.claude/skills/manuscript-template/scripts/md2docx.py paper/manuscript_EnB.md -o paper/manuscript_EnB.docx
python ~/.claude/skills/manuscript-template/scripts/md2docx.py paper/supplementary_EnB.md -o paper/supplementary_EnB.docx
python ~/.claude/skills/manuscript-template/scripts/check_format.py paper/manuscript_EnB.docx
python ~/.claude/skills/manuscript-template/scripts/split_submission.py paper/manuscript_EnB.docx --out-dir paper/submission --line-numbers
```

Once the `.docx` is edited in Word, it becomes the source; do not regenerate it from the Markdown. In Word, choose the
journal's output style in EndNote and run EndNote > Update Citations and Bibliography (the citations carry their own
records, so EndNote > Export Traveling Library adds them to your library).

Where the content comes from:

| Paper item | Source in this repository |
|---|---|
| Figs. 1–3 (schematics) and 4–8 (results), graphical abstract | `docs/paper/figures/`, made by `case_studies/paper_figures.py` |
| Numbers in the text of Sections 5.1–5.7 | `docs/paper_numbers.md`, made by `case_studies/paper_numbers.py` |
| Tables 6, 7, 8, 9 | `docs/zone_sweep.md`, `docs/topology_survey.md`, `docs/morris.md`, `docs/annual.md` |
| Benchmarks (Section 5.6) | `docs/benchmark.md` |
| Table 5 | `case_studies/B_hvac_system/output/summary.json` (after a build) |

## Revision of 30 October 2026 (after a mock review for Energy and Buildings)

A mock editorial and peer review (desk screening, three referee reports, decision "major revision") asked for an
explicit link to building energy use, benchmarks of the element models, a correctness score for the tracer, a sharper
provenance definition and a global sensitivity analysis. The revision:

- **Reframed** the paper around simulation-grounded thermography for building diagnostics (title, abstract,
  highlights, introduction, discussion, conclusions), with robots and drones as one consumer of the data; the untested
  Isaac Sim script is no longer a contribution.
- **Energy relevance of the faults (new E7, Table 9):** one-year runs of case B (Phoenix) and case C (Chicago): how often
  each fault is visible to a default camera and what it does to the building's heat flows. The duct gap is the more
  visible and the less important fault.
- **Benchmarks (Section 5.6):** element models against natural-convection correlations; the 1-D envelope void against a
  2-D conduction section.
- **Tracer correctness (Table 7):** traced chains compared component by component with the chains declared by each
  model's system objects (precision and recall, identical chains); the plenum is detected without names.
- **Provenance:** decision rules for the six levels; assumed material properties are heuristic; provenance maps are
  computed per frame (jet influence ≥ 0.1 K, stagnant fluid).
- **Sensitivity (Table 8):** Morris screening of 14 parameters on 8 contrasts, including the camera settings; the
  one-at-a-time table moved to the Supplementary Material.
- **Code fixes** behind these results (see the git log), all constants moved to `thermoscene/defaults.yaml`,
  continuous integration that rebuilds the cases with EnergyPlus 22.2 and checks the paper numbers.
- **Related work** extended (quantitative and drone thermography, deep-learning anomaly detection, infrared scene
  simulation, fault modelling and FDD datasets, eplusout.bnd); acronyms defined; generative-AI declaration added.

Still open for the authors: the author list (a placeholder student author is in the title block), acknowledgements and
funding, the competing-interest statement for the Big Ladder Software affiliation, a Zenodo DOI for the submitted code version
(the repository is public since 6 November 2026), the journal's reference style in EndNote, and a check against the current Guide for
Authors (it could not be downloaded automatically).
