"""Command line:  python -m thermoscene {build,auto,render,web,dataset,episode,inspect} ..."""
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
    d = sub.add_parser("dataset", help="export a labelled thermal-image dataset (COCO-style) from a scene")
    d.add_argument("usd"); d.add_argument("-o", "--out", required=True); d.add_argument("--views", type=int, default=100)
    d.add_argument("--camera", default="handheld_320"); d.add_argument("--seed", type=int, default=0)
    d.add_argument("--fixed-camera", action="store_true", help="one camera unit for all views (no randomisation)")
    d.add_argument("--spaces", nargs="*", default=["Zone", "Plenum", "Outdoors"])
    e = sub.add_parser("episode", help="run one inspection episode with a random agent and save its frames (demo)")
    e.add_argument("usd"); e.add_argument("-o", "--out", required=True); e.add_argument("--space", default="Zone")
    e.add_argument("--task", default="fault_search"); e.add_argument("--steps", type=int, default=20)
    e.add_argument("--camera", default="handheld_320"); e.add_argument("--seed", type=int, default=0)
    i = sub.add_parser("inspect", help="list zones of an EnergyPlus model and trace the HVAC serving them")
    i.add_argument("idf"); i.add_argument("--zone"); i.add_argument("--energyplus")
    u = sub.add_parser("auto", help="build scenes from any EnergyPlus model without a case file (writes auto_case.yaml)")
    u.add_argument("idf")
    u.add_argument("--epw", help="weather file (default: the EnergyPlus WeatherData file nearest to Site:Location)")
    u.add_argument("--zone", help="zone name, 'largest' or 'all' (default: the conditioned zone with the longest "
                                  "traced HVAC chain, ties broken by floor area)")
    u.add_argument("--out", help="output folder (default: ./auto_output/<model>)")
    u.add_argument("--render", action="store_true", help="also run the reference renderer")
    u.add_argument("--energyplus")
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
    elif a.cmd == "dataset":
        from .embodied.dataset import export_dataset
        out = export_dataset(a.usd, a.out, a.views, a.camera, not a.fixed_camera, a.seed, tuple(a.spaces))
        print("wrote", out)
    elif a.cmd == "episode":
        from pathlib import Path
        from PIL import Image
        from .embodied.env import InspectionEnv
        env = InspectionEnv(a.usd, camera=a.camera, space=a.space, task=a.task, max_steps=a.steps)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        obs, info = env.reset(seed=a.seed)
        total = 0.0
        for k in range(a.steps):
            obs, r, term, trunc, info = env.step(env.action_space.sample())
            total += r
            Image.fromarray(env.render()).save(out / f"step_{k:03d}.png")
            if term or trunc:
                break
        print(f"{k + 1} steps, return {total:.2f}, faults found {len(info['found'])}/{info['n_faults']}; frames in {out}")
    elif a.cmd == "auto":
        from . import auto
        for b in auto.run(a.idf, a.epw, a.zone, a.out, a.render, exe=a.energyplus):
            print(f"zone {b['zone']}: " + (f"FAILED at {b['error']}" if "error" in b else b["usd"])
                  + f"  (weather {b['epw']}; configuration {b['case']})")
    else:
        print(pipeline.inspect(a.idf, a.zone, a.energyplus))


if __name__ == "__main__":
    main()
