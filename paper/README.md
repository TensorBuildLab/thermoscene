# Manuscript

*Virtual thermal training environments for robots from building energy models: an ontology-driven pipeline for
buildings and HVAC systems* (draft for Energy and Buildings / Building and Environment).

| File | Content |
|---|---|
| **`manuscript_EnB.docx`** | **current version**, formatted for Energy and Buildings: Times New Roman 12 pt, double spacing, numbered headings, real Word equations numbered at the margin, three-line tables, Fig./Table captions with live cross-references, numbered references as EndNote fields |
| `manuscript_EnB.md` | its source (template Markdown: `[@key]` citations, `$$ … $$ {#eq:…}` equations, `@fig:`/`@tbl:`/`@sec:` cross-references) |
| `references.ris` | the 27 references with complete author lists and DOIs (cited keys come from here) |
| `manuscript.docx`, `manuscript.md` | the earlier export of the same revised text in author-year style (equations as LaTeX text) |

Rebuild the formatted file after editing `manuscript_EnB.md` (the `manuscript-template` Claude skill):

```bash
python ~/.claude/skills/manuscript-template/scripts/md2docx.py paper/manuscript_EnB.md -o paper/manuscript_EnB.docx
python ~/.claude/skills/manuscript-template/scripts/check_format.py paper/manuscript_EnB.docx
```

Once the `.docx` is edited in Word, it becomes the source; do not regenerate it from the Markdown. In Word, choose the
journal's output style in EndNote and run EndNote > Update Citations and Bibliography (the citations carry their own
records, so EndNote > Export Traveling Library adds them to your library).

Before submitting to Energy and Buildings: prepare the graphical abstract (required), check whether line numbers are
requested, and note the manuscript runs to about 42 double-spaced pages including 8 figures and 8 tables.

Where the content comes from:

| Paper item | Source in this repository |
|---|---|
| Figs. 1–3 (schematics) and 4–8 (results) | `docs/paper/figures/`, made by `case_studies/paper_figures.py` |
| Numbers in the text of Sections 5.1–5.6 | `docs/paper_numbers.md`, made by `case_studies/paper_numbers.py` |
| Tables 6, 7, 8 | `docs/zone_sweep.md`, `docs/topology_survey.md`, `docs/sensitivity.md` |
| Table 5 | `case_studies/B_hvac_system/output/summary.json` (after a build) |

## Revision of 29 October 2026

All results were regenerated with EnergyPlus 22.2 (Windows 11, Python 3.12) and the text was brought in line with them
and with the code:

- **Results.** Element counts, the topology survey (180/180), the zone sweep (15/15) and the sensitivity table are
  unchanged. A few case B values moved: the morning-reheat diffuser (23.1 °C, supply air 23.7 °C at 07:30), the pipe
  jacket during reheat (26.7 °C), the duct-gap contrast during reheat (+1.5 K), the winter-night roof contrast
  (−4.1 K, now stated at 03:00) and the wall contrasts by 0.1 K. Run times and the case C time-sample size (48 MB)
  were re-measured on a laptop CPU.
- **Method text matched to the code.** IDF input only; the terminal box carries the damper-outlet node; cooling towers
  and reheat-coil sources are traced but not drawn; `bare_pipe` uses a fixed 3 % film ratio; sol-air, condenser and jet
  parameters are stated; the louver model is listed; the jet amplitude is renamed `a_max` (it clashed with `A_max` in
  Eq. 1); the web viewer agrees with the reference renderer to within 10⁻³ K, without optical blur.
- **References** were checked against Crossref, arXiv, OSTI, DataCite and publisher records: DOIs added; HEARTH is
  Su et al. (2026), ADAPT is Yang et al. (2026), MEP-seg is Baubriaud (2023); ASHRAE 223P is cited as the 2026
  public-review draft; ProcTHOR author order, BEHAVIOR (2022, PMLR 164), HM3D, Goldfeder et al. and Prabowo et al.
  completed.

Still open for the authors: the author list and corresponding author, CRediT/acknowledgement/competing-interest
sections, Big Ladder Software's city, conversion to the journal's reference style, and real Word equations (the
equations are LaTeX text).
