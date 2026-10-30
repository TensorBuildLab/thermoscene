"""Assemble the Supplementary Material from the result tables that the case_studies scripts write into docs/.

    python paper/build_supplementary.py            -> paper/supplementary_EnB.md
    python <manuscript-template>/scripts/md2docx.py paper/supplementary_EnB.md -o paper/supplementary_EnB.docx
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TITLE = ("Supplementary Material for: From energy models to thermal scenes: an ontology-driven pipeline for physically "
         "grounded thermography of buildings and HVAC systems")


def section(md_file, title, intro, drop_title=True):
    text = (DOCS / md_file).read_text(encoding="utf-8")
    lines = text.splitlines()
    if drop_title and lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = []
    for line in lines:
        if line.startswith("## "):
            body.append("##" + line[1:])                       # demote sub-headings one level
        else:
            body.append(line.replace("`", ""))                  # code spans would carry a monospace font
    caption_done = False
    out = [f"# {title}", "", intro, ""]
    for line in body:
        if line.startswith("|") and not caption_done:          # the first table gets a caption
            out += [f"Table: {title}.", ""]
            caption_done = True
        out.append(line)
    return "\n".join(out).strip() + "\n"


def main():
    parts = ["---", f"title: {TITLE}", "---", ""]
    parts.append(section("sensitivity.md", "One-at-a-time sensitivity of apparent contrasts (case B)",
                         "Each parameter set to its low and high value while all others stay at their defaults. The Morris "
                         "screening over the same parameters and contrasts is Table 8 of the paper."))
    parts.append(section("benchmark.md", "Benchmarks of the physics-level models",
                         "Pipeline surface temperatures compared with an independent steady calculation using natural "
                         "convection from horizontal cylinders (Churchill and Chu) and linearised radiation; stagnation time constants "
                         "compared with lumped-capacitance estimates; one-dimensional "
                         "envelope defect compared with a two-dimensional section."))
    parts.append(section("annual.md", "Faults over a full year (cases B and C)", "Statistics behind Table 9 of the paper."))
    parts.append(section("zone_sweep.md", "Zone sweep over the unmodified DOE reference medium office",
                         "One scene per conditioned zone, one shared EnergyPlus run; the per-group summary is Table 6."))
    parts.append(section("topology_survey.md", "Topology tracing on EnergyPlus example models, per model",
                         "Classes found per model and the zones whose traced chain differs from the declared structure; the "
                         "summary is Table 7."))
    text = "\n".join(parts)
    out = ROOT / "paper" / "supplementary_EnB.md"
    out.write_text(text, encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
