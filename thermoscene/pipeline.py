"""End-to-end pipeline: EnergyPlus model + case configuration -> thermal scene (USD + ontology graph)."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from . import config as config_mod
from .energyplus.model import EPModel
from .energyplus.results import Results
from .energyplus.runner import energyplus_dir, prepare, simulate, to_epjson
from .energyplus.topology import HVACGraph
from .generators import air_devices, distribution, equipment
from .generators.common import Gen
from .ontology import Element, SceneGraph, SimComponent, Surface
from .render.cameras import build_cameras
from .render.raycast import accel, raycast, render_usd
from .scene import faults as faults_mod
from .scene import spaces
from .thermal import environment, models
from .usd.writer import Writer, scene_meshes

log = logging.getLogger("thermoscene")


def expand_only(idf, exe, outdir) -> Path:
    """Run ExpandObjects alone (HVACTemplate -> full objects) without simulating."""
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(idf, outdir / "in.idf")
    for f in ("Energy+.idd",):
        shutil.copy(energyplus_dir(exe) / f, outdir / f)
    subprocess.run([str(energyplus_dir(exe) / "ExpandObjects")], cwd=outdir, capture_output=True)
    return outdir / "expanded.idf" if (outdir / "expanded.idf").exists() else Path(idf)


def load_model(idf, exe, work) -> EPModel:
    from .energyplus.idf_text import IDFText
    src = Path(idf)
    if any(c.startswith("HVACTEMPLATE:") for c in IDFText.read(src).classes()):
        src = expand_only(src, exe, Path(work) / "expand")
    return EPModel(to_epjson(src, exe, Path(work) / "epjson"))


def _cached_simulation(prepared, epw, out, exe, cache_root):
    """Re-use an EnergyPlus run when the prepared model, weather file and engine are unchanged."""
    import hashlib
    h = hashlib.sha256()
    for f in (prepared, epw):
        h.update(Path(f).read_bytes())
    h.update(str(exe).encode())
    key = h.hexdigest()[:16]
    cdir = Path(cache_root) / key
    if (cdir / "eplusout.sql").exists():
        log.info("EnergyPlus: re-using cached run %s", key)
        exp = cdir / "eplusout.expidf"
        err = (cdir / "eplusout.err").read_text(errors="ignore")
        return dict(sql=cdir / "eplusout.sql", err=cdir / "eplusout.err", model_idf=exp if exp.exists() else prepared,
                    warnings=err.count("** Warning **"), severe=err.count("** Severe  **"), cached=True)
    sim = simulate(prepared, epw, cdir, exe)
    for f in cdir.iterdir():                      # keep only what the pipeline reads
        if f.name not in ("eplusout.sql", "eplusout.err", "eplusout.expidf"):
            f.unlink()
    sim["cached"] = False
    return sim


def build(case_path, render: bool = False, overrides: dict | None = None) -> Path:
    t0 = time.time()
    cfg = config_mod.load(case_path)
    if overrides:
        cfg = config_mod.deep_merge(cfg, overrides)
        if "output_dir" in overrides:
            cfg["output_dir"] = str(Path(overrides["output_dir"]).resolve())
    out = Path(cfg["output_dir"]); out.mkdir(parents=True, exist_ok=True)
    exe = cfg["energyplus"]["executable"]
    log.info("case %s -> %s", cfg["name"], out)

    # 1. faults that must be simulated are injected into the user's model
    pre = load_model(cfg["model"]["idf"], exe, out / "_work" / "pre")
    extra, defects = faults_mod.injection_objects(pre, cfg.get("faults", []))
    prepared = out / "prepared.idf"
    prepare(cfg["model"]["idf"], prepared, cfg["energyplus"].get("run_periods"), extra, cfg["energyplus"].get("timesteps_per_hour"))
    # 2. simulate (cached) and read
    cache = cfg["energyplus"].get("cache_dir") or str(Path(cfg["case_dir"]) / ".sim_cache")
    sim = _cached_simulation(prepared, cfg["model"]["epw"], out, exe, cache)
    log.info("EnergyPlus: %d warnings, %d severe", sim["warnings"], sim["severe"])
    tph = cfg["energyplus"].get("timesteps_per_hour")
    results = Results(sim["sql"], 60 // int(tph) if tph else None)
    model = EPModel(to_epjson(sim["model_idf"], exe, out / "_work" / "post"))
    graph = HVACGraph(model)
    # 3. spaces, surfaces, topology
    scene = SceneGraph()
    scene.meta["source"] = f"EnergyPlus {Path(exe).parent.name}; model {Path(cfg['model']['idf']).name}; weather {Path(cfg['model']['epw']).name}"
    ids = spaces.build(scene, model, cfg)
    zone_name = scene.get(ids["zone"]).sim_zone
    chain = graph.serving(zone_name) if zone_name.upper() in graph.zone_inlets else {"components": [], "inlet_nodes": [], "loops": []}
    sizes = results.component_sizes()
    for c, d in chain["components"]:
        c.sizes = sizes.get(c.name.upper(), {})
        c.attrs["distance"] = d
        scene.add(c)
    scene.feeds = graph.feeds_edges({c.id for c, _ in chain["components"]})
    # 4. generators
    gen = Gen(scene, graph, results, cfg, model, ids, chain)
    el_faults = [f for f in cfg.get("faults", []) if f["type"] not in ("envelope_defect", "energyplus_object")]
    devices = air_devices.generate(gen)
    dist = distribution.generate(gen, devices, el_faults)
    equipment.generate(gen, dist)
    # 5. thermal
    env = environment.outdoor(results, model, cfg["model"]["epw"])
    environment.assign_space_environments(scene, results, env)
    models.assign(scene, models.Ctx(scene, results, cfg, env))
    # 6. cameras (explicit or automatic)
    def meshes_fn():
        pairs = scene_meshes(scene)
        return accel([f for _, f in pairs]), [t.id for t, _ in pairs]
    build_cameras(scene, ids, cfg, results.labels, meshes_fn, raycast)
    # 7. write
    usd = out / f"{cfg['name']}.usda"
    if usd.exists():
        usd.unlink()
    Writer(scene, cfg, results.labels, results.step_minutes).write(usd)
    (out / "scene.ttl").write_text(scene.to_turtle())
    summary = _summary(scene, cfg, ids, chain, sim, results)
    summary["build_seconds"] = round(time.time() - t0, 2)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    log.info("wrote %s (%.0f s)", usd, time.time() - t0)
    if render:
        render_usd(usd, out / "renders")
    return usd


def _summary(scene, cfg, ids, chain, sim, results):
    prov = Counter()
    for t in scene.things.values():
        for q, (lvl, _) in t.provenance.items():
            prov[f"{type(t).__name__}.{q}:{lvl.value}"] += 1
    return dict(
        case=cfg["name"], zone=ids["zone"], plenum=ids["plenum"], timesteps=results.n, step_minutes=results.step_minutes,
        energyplus=dict(warnings=sim["warnings"], severe=sim["severe"]),
        serving_chain=[dict(cls=c.cls, type=c.sim_type, name=c.name, role=c.role, system=c.system, distance=d)
                       for c, d in chain["components"]],
        counts=dict(surfaces=len(scene.of(Surface)), elements=len(scene.of(Element)), components=len(scene.of(SimComponent))),
        elements=[dict(id=e.id, cls=e.cls, model=e.thermal_model, material=e.material, size=e.attrs.get("size", ""),
                       stream=e.stream_node, provenance={q: l.value for q, (l, _) in e.provenance.items()})
                  for e in scene.of(Element)],
        provenance_counts=dict(sorted(prov.items())))


def inspect(idf, zone=None, exe=None) -> str:
    from .energyplus.runner import find_energyplus
    import tempfile
    exe = find_energyplus(exe)
    with tempfile.TemporaryDirectory() as tmp:
        m = load_model(idf, exe, tmp)
        g = HVACGraph(m)
        lines = [f"{len(m.zone_names())} zones, {len(g.comps)} HVAC components, conditioned zones: {len(g.zone_inlets)}"]
        for z in ([zone] if zone else m.zone_names()):
            srf = m.surfaces(z)
            lines.append(f"\nZone {z}: {len(srf)} surfaces, multiplier {m.zone_multiplier(z)}, plenum above: "
                         f"{spaces.find_plenum(m, z, 'auto')}")
            if z.upper() in g.zone_inlets:
                lines.append(g.summary(z))
        return "\n".join(lines)
