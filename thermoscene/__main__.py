"""Command line:  python -m thermoscene {build,render,web,inspect} ..."""
import argparse
import logging
import sys

from . import pipeline


def main():
    ap = argparse.ArgumentParser(prog="thermoscene")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a thermal scene from a case configuration")
    b.add_argument("case"); b.add_argument("--render", action="store_true", help="also run the reference renderer")
    r = sub.add_parser("render", help="reference-render the cameras of an existing scene")
    r.add_argument("usd"); r.add_argument("--out", default=None); r.add_argument("--cameras", nargs="*")
    w = sub.add_parser("web", help="export a scene to a self-contained real-time thermal viewer (HTML)")
    w.add_argument("usd"); w.add_argument("-o", "--out", default=None); w.add_argument("--title", default=None)
    i = sub.add_parser("inspect", help="list zones of an EnergyPlus model and trace the HVAC serving them")
    i.add_argument("idf"); i.add_argument("--zone"); i.add_argument("--energyplus")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):          # zone and object names may not fit the console code page (Windows)
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if a.cmd == "build":
        pipeline.build(a.case, render=a.render)
    elif a.cmd == "render":
        from pathlib import Path
        from .render.raycast import render_usd
        render_usd(a.usd, a.out or Path(a.usd).parent / "renders", a.cameras)
    elif a.cmd == "web":
        from pathlib import Path
        from .web.export import export
        out = export(a.usd, a.out or Path(a.usd).with_suffix(".viewer.html"), a.title)
        print("wrote", out, f"({out.stat().st_size / 1e6:.1f} MB)")
    else:
        print(pipeline.inspect(a.idf, a.zone, a.energyplus))


if __name__ == "__main__":
    main()
