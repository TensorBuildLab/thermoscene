"""Corpus survey: thermoscene on EVERY EnergyPlus example file (generality of the tracer and of the whole pipeline).

Stage `topology` (no simulation): load each model (ExpandObjects when needed, epJSON), build the HVAC graph, trace every
conditioned zone and compare with the declared reference chain (`topology_survey.declared_chain`).
Stage `scenes` (with simulation): `thermoscene auto` on every model (weather file nearest to Site:Location, two 2-day run
periods, default zone, automatic cameras), rendered with one preview frame per camera.
Stage `report`: docs/corpus_survey.md from both result files.

Results are resumable JSON files (case_studies/corpus_survey.json, case_studies/corpus_scenes.json): a model already
recorded is skipped unless --redo (all) or --failed (only failed ones) is given. Every model runs in its own worker
process with a time limit, so a crash or a hang costs one model, not the run.

Run:  python case_studies/corpus_survey.py topology [--jobs 3] [--only PATTERN ...] [--redo | --failed]
      python case_studies/corpus_survey.py scenes   [--jobs 3] [--only PATTERN ...] [--redo | --failed] [--keep]
      python case_studies/corpus_survey.py report
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "case_studies"))
TOPO_JSON = ROOT / "case_studies" / "corpus_survey.json"
SCENE_JSON = ROOT / "case_studies" / "corpus_scenes.json"
SCENE_DIR = ROOT / "case_studies" / "corpus"                 # git-ignored outputs of the scenes stage
REPORT = ROOT / "docs" / "corpus_survey.md"
TIMEOUT = {"topology": 600, "scenes": 3600}                  # s per model
STAGGER_S = {"topology": 1.0, "scenes": 5.0}               # between worker starts (EnergyPlus start-up on Windows)
BATCH = {"topology": 12, "scenes": 1}                        # models per worker process (start-up costs ~5 s)

# ---------------------------------------------------------------- system types, from object types only (never names)
VAV_TERMINALS = ("AirTerminal:SingleDuct:VAV", "AirTerminal:SingleDuct:SeriesPIU", "AirTerminal:SingleDuct:ParallelPIU")
ZONE_SYSTEMS = [       # (tag, object-type prefixes); see system_tags for the combined VAV tags
    ("constant-volume air loop", ("AirTerminal:SingleDuct:ConstantVolume:Reheat", "AirTerminal:SingleDuct:ConstantVolume:NoReheat",
                                  "AirTerminal:SingleDuct:Uncontrolled")),
    ("dual duct", ("AirTerminal:DualDuct",)),
    ("induction / chilled beam", ("AirTerminal:SingleDuct:ConstantVolume:FourPipeInduction",
                                  "AirTerminal:SingleDuct:ConstantVolume:CooledBeam", "AirTerminal:SingleDuct:ConstantVolume:FourPipeBeam")),
    ("unitary (air loop)", ("AirLoopHVAC:UnitarySystem", "AirLoopHVAC:Unitary", "CoilSystem:")),
    ("PTAC / PTHP", ("ZoneHVAC:PackagedTerminal",)),
    ("water-to-air heat pump", ("ZoneHVAC:WaterToAirHeatPump", "Coil:Cooling:WaterToAirHeatPump", "Coil:Heating:WaterToAirHeatPump")),
    ("fan coil", ("ZoneHVAC:FourPipeFanCoil",)),
    ("VRF", ("AirConditioner:VariableRefrigerantFlow", "ZoneHVAC:TerminalUnit:VariableRefrigerantFlow")),
    ("window AC", ("ZoneHVAC:WindowAirConditioner",)),
    ("unit heater / ventilator", ("ZoneHVAC:UnitHeater", "ZoneHVAC:UnitVentilator")),
    ("radiant", ("ZoneHVAC:LowTemperatureRadiant", "ZoneHVAC:HighTemperatureRadiant", "ZoneHVAC:CoolingPanel")),
    ("baseboard", ("ZoneHVAC:Baseboard",)),
    ("ventilated slab", ("ZoneHVAC:VentilatedSlab",)),
    ("outdoor-air unit / ERV / DOAS", ("ZoneHVAC:OutdoorAirUnit", "ZoneHVAC:EnergyRecoveryVentilator",
                                       "AirLoopHVAC:DedicatedOutdoorAirSystem")),
    ("evaporative cooling", ("EvaporativeCooler:", "ZoneHVAC:EvaporativeCoolerUnit")),
    ("dehumidifier", ("ZoneHVAC:Dehumidifier",)),
    ("hybrid / user-defined unit", ("ZoneHVAC:HybridUnitaryHVAC", "ZoneHVAC:ForcedAir:UserDefined", "AirTerminal:SingleDuct:UserDefined")),
    ("ideal loads", ("ZoneHVAC:IdealLoadsAirSystem",)),
    ("refrigeration", ("Refrigeration:", "ZoneHVAC:RefrigerationChillerSet")),
    ("zone exhaust fan", ("Fan:ZoneExhaust",)),
]
PLANT_SYSTEMS = [
    ("chiller plant", ("Chiller:", "ChillerHeater:")),
    ("boiler plant", ("Boiler:",)),
    ("district energy", ("DistrictCooling", "DistrictHeating")),
    ("plant heat pump", ("HeatPump:WaterToWater", "HeatPump:PlantLoop")),
    ("ground heat exchanger", ("GroundHeatExchanger:",)),
    ("thermal storage", ("ThermalStorage:",)),
    ("water heater", ("WaterHeater:",)),
]


def system_tags(types) -> list[str]:
    """System-type tags of a set of EnergyPlus object types (a model's, or the types in one zone's chain)."""
    t = {x.upper() for x in types}
    has = lambda *prefixes: any(x.startswith(p.upper()) for x in t for p in prefixes)
    tags = []
    if has(*VAV_TERMINALS):
        if has("Coil:Cooling:Water"):
            tags.append("central VAV (chilled water)")
        if has("Coil:Cooling:DX", "CoilSystem:Cooling:DX"):
            tags.append("packaged VAV (DX)")
        if not has("Coil:Cooling:Water", "Coil:Cooling:DX", "CoilSystem:Cooling"):
            tags.append("VAV (other cooling)")
    tags += [tag for tag, p in ZONE_SYSTEMS if has(*p)]
    tags += [tag for tag, p in PLANT_SYSTEMS if has(*p)]
    if has("AirflowNetwork:SimulationControl"):
        tags.append("airflow network")
    return tags


def out_of_scope(idf, n_zones: int | None = None) -> str | None:
    """Model features thermoscene cannot or should not handle on its own (checked on the raw IDF text)."""
    c = idf.classes()
    if any(x.startswith("PARAMETRIC:") for x in c):
        return "Parametric:* objects (must be run through ParametricPreprocessor first)"
    if any(x.startswith("EXTERNALINTERFACE") for x in c):
        return "ExternalInterface co-simulation (FMU/BCVTB)"
    if "ZONE" not in c or n_zones == 0:
        return "no zones (plant, component or weather test file)"
    return None


def version_reason(idf) -> str | None:
    """For a model that FAILED: written for another EnergyPlus version (several older files still run)."""
    from thermoscene.energyplus.runner import engine_version, find_energyplus
    ver, eng = idf.version(), engine_version(find_energyplus()).split()[-1]
    if ver and ".".join(ver.split(".")[:2]) != ".".join(eng.split(".")[:2]):
        return f"written for EnergyPlus {ver} (needs IDFVersionUpdater to {eng})"
    return None


# ---------------------------------------------------------------- stage 1: topology (one model)
def _named_children(m, g, ref: set) -> set:
    """Components that a declared object names in a '<x>_name' field without an object-type field (a PIU's fan and
    mixer, an energy recovery ventilator's heat exchanger and fans): parts the reference does not follow."""
    names = {c.name.upper(): k for k, c in g.comps.items()}
    out = set()
    for rk in ref:
        c = g.comps.get(rk)
        _, f = m.obj(c.sim_type, c.name) if c else (None, None)
        for k, v in (f or {}).items():
            if k.endswith("_name") and isinstance(v, str) and v.upper() in names:
                out.add(names[v.upper()])
    return out


def topology_one(idf: Path) -> dict:
    from topology_survey import declared_chain

    from thermoscene.energyplus.idf_text import IDFText
    from thermoscene.energyplus.runner import find_energyplus
    from thermoscene.energyplus.topology import HVACGraph
    from thermoscene.pipeline import load_model
    t0 = time.time()
    exe = find_energyplus()
    raw = IDFText.read(idf)
    rec = dict(file=idf.name, ok=False, out_of_scope=out_of_scope(raw), expanded=False)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            m = load_model(idf, exe, tmp)
            rec["expanded"] = (Path(tmp) / "expand" / "expanded.idf").exists()
        except Exception as e:  # noqa: BLE001
            rec["out_of_scope"] = rec["out_of_scope"] or version_reason(raw)
            rec.update(stage="load", error_type=type(e).__name__, error=str(e)[-1500:], seconds=round(time.time() - t0, 2),
                       tags=system_tags(raw.classes()))
            return rec
    rec["tags"] = system_tags(m.classes())
    try:
        g = HVACGraph(m)
    except Exception as e:  # noqa: BLE001
        rec.update(stage="graph", error_type=type(e).__name__, error=str(e)[-1500:], seconds=round(time.time() - t0, 2))
        return rec
    zones = []
    for z in g.zone_inlets:
        zr = dict(zone=z)
        try:
            s = g.serving(z)
            found = {c.id for c, _ in s["components"]}
            types = {c.sim_type for c, _ in s["components"]}
            classes = sorted({c.cls for c, _ in s["components"]})
        except KeyError:                                 # zone with an equipment connection but no nodes
            found, types, classes = set(), set(), []
        try:
            ref = declared_chain(m, z)
        except Exception as e:  # noqa: BLE001
            zr["reference_error"] = f"{type(e).__name__}: {e}"
            ref = set()
        types_all = types | ref                          # 'TYPE:NAME' keys: system_tags matches type prefixes
        zr.update(traced=bool(found), n=len(found), tp=len(found & ref), fp=len(found - ref), fn=len(ref - found),
                  exact=found == ref, classes=classes, tags=system_tags(types_all) or ["other"],
                  fp_items=sorted(found - ref)[:40], fn_items=sorted(ref - found)[:40],
                  fp_named=len(_named_children(m, g, ref) & (found - ref)))
        zones.append(zr)
    rec.update(ok=True, zones=len(m.zone_names()), conditioned=len(g.zone_inlets), components=len(g.comps),
               traced=sum(z["traced"] for z in zones), tp=sum(z["tp"] for z in zones), fp=sum(z["fp"] for z in zones),
               fn=sum(z["fn"] for z in zones), exact=sum(z["exact"] for z in zones),
               mean_chain=round(sum(z["n"] for z in zones if z["traced"]) / max(1, sum(z["traced"] for z in zones)), 1),
               classes=dict(Counter(c for z in zones for c in z["classes"])), zone_results=zones,
               warnings=m.warnings, seconds=round(time.time() - t0, 2))
    if rec["zones"] == 0:
        rec["out_of_scope"] = out_of_scope(raw, 0)
    return rec


# ---------------------------------------------------------------- stage 2: scenes (one model)
def unmodified_run(idf: Path, epw) -> dict:
    """Run the UNMODIFIED example file (design days only, same weather file) to tell whether a simulation failure is
    EnergyPlus's own (the model as shipped does not run) or caused by thermoscene's changes (run periods, outputs)."""
    from thermoscene.energyplus.idf_text import IDFText
    from thermoscene.energyplus.runner import err_tail, find_energyplus, needs_expand
    exe = find_energyplus()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cmd = [exe, "-D", "-w", str(epw), "-d", tmp] + (["-x"] if needs_expand(IDFText.read(idf)) else []) + [str(idf)]
        try:
            p = subprocess.run(cmd, check=False, capture_output=True, text=True, errors="replace", cwd=str(idf.parent), timeout=900)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            return dict(fails=None, note="timeout")
        err = (Path(tmp) / "eplusout.err").read_text(encoding="utf-8", errors="ignore") if (Path(tmp) / "eplusout.err").exists() else ""
    fails = rc != 0 or "**  Fatal  **" in err
    return dict(fails=fails, error=err_tail(err, 600) if fails else "")


def scene_dir(idf: Path) -> Path:
    """Output folder of one model: short (Windows paths are limited to 260 characters) and unique."""
    import hashlib
    return SCENE_DIR / f"{idf.stem[:36]}_{hashlib.sha1(idf.name.encode()).hexdigest()[:4]}"


def scene_one(idf: Path, keep: bool = False) -> dict:
    from thermoscene import auto
    from thermoscene.energyplus.idf_text import IDFText
    t0 = time.time()
    raw = IDFText.read(idf)
    out = scene_dir(idf)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    rec = dict(file=idf.name, ok=False, out_of_scope=out_of_scope(raw))
    try:
        res = auto.run(idf, out_dir=out, render=True, preview_frames=1, cache_dir=SCENE_DIR / ".sim_cache")[0]
        s = json.loads((Path(res["output_dir"]) / "summary.json").read_text(encoding="utf-8"))
        renders = sorted(p.name for p in (Path(res["output_dir"]) / "renders").glob("preview_*.png"))
        rec.update(ok=True, stage="done", zone=res["zone"], epw=Path(res["epw"]).name, plenum=s["plenum"],
                   counts=s["counts"], chain=len(s["serving_chain"]), timesteps=s["timesteps"],
                   step_minutes=s["step_minutes"], energyplus=s["energyplus"], cameras=[c["name"] for c in s.get("cameras", [])],
                   camera_targets=[c.get("auto", {}).get("target", "") for c in s.get("cameras", [])],
                   renders=len(renders), build_seconds=s["build_seconds"],
                   element_classes=dict(Counter(e["cls"] for e in s["elements"])))
    except Exception as e:  # noqa: BLE001
        rec.update(stage=getattr(e, "thermoscene_stage", "auto"), error_type=type(e).__name__, error=str(e)[-2000:])
        if rec["stage"] in ("expand", "simulate"):        # an older model may still run; only a failure counts
            rec["out_of_scope"] = rec["out_of_scope"] or version_reason(raw)
        info = getattr(e, "thermoscene_info", {}) or {}
        rec.update({k: v for k, v in info.items() if k in ("zone", "epw", "energyplus")})
        if rec["stage"] == "simulate":                 # the message carries the Severe/Fatal lines of eplusout.err
            rec["energyplus"] = dict(severe=str(e).count("** Severe  **"), fatal=str(e).count("**  Fatal  **"))
            if rec.get("epw"):
                rec["unmodified"] = unmodified_run(idf, rec["epw"])
    rec["seconds"] = round(time.time() - t0, 1)
    sim = SCENE_DIR / ".sim_cache"
    import gc
    gc.collect()                               # close the build's SQLite connection (Windows cannot delete open files)
    if rec["ok"] and not keep:                 # keep summaries, configuration and previews; drop the bulky outputs
        for d in out.glob("**/_work"):
            shutil.rmtree(d, ignore_errors=True)
        for pat in ("**/*.usda", "**/*.npz", "**/renders/*/*.npy", "**/scene.ttl", "**/prepared.idf"):
            for f in out.glob(pat):
                f.unlink(missing_ok=True)
        key = rec.get("energyplus", {}).get("cache_key")
        if key and (sim / key).exists():
            shutil.rmtree(sim / key, ignore_errors=True)
    return rec


# ---------------------------------------------------------------- driver: one worker process per model
def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(data.items())), indent=1), encoding="utf-8")
    for attempt in range(20):                  # Windows: a scanner or indexer may hold the file for a moment
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.5)
    path.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")


def corpus(only=None) -> list[Path]:
    from thermoscene.energyplus.runner import energyplus_dir, find_energyplus
    files = sorted((energyplus_dir(find_energyplus()) / "ExampleFiles").glob("*.idf"), key=lambda p: p.name.lower())
    if only:
        files = [f for f in files if any(fnmatch.fnmatch(f.name.lower(), p.lower()) or p.lower() in f.name.lower() for p in only)]
    return files


def drive(stage: str, jobs: int, only=None, redo=False, failed=False, keep=False) -> dict:
    path = TOPO_JSON if stage == "topology" else SCENE_JSON
    data = _load(path)
    files = corpus(only)
    if stage == "scenes" and not only:            # only models that loaded in stage 1 (others are recorded as such)
        topo = _load(TOPO_JSON)
        for f in files:
            t = topo.get(f.name)
            if t and not t["ok"] and f.name not in data:
                data[f.name] = dict(file=f.name, ok=False, stage="topology", error_type=t.get("error_type"),
                                    error="model did not load in the topology stage: " + (t.get("error") or "")[-500:],
                                    out_of_scope=t.get("out_of_scope"))
        _save(path, data)
    todo = [f for f in files if redo or f.name not in data or (failed and not data[f.name].get("ok"))]
    print(f"{stage}: {len(files)} models, {len(todo)} to run, {jobs} workers", flush=True)
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    running: list[dict] = []
    t_last, done = 0.0, 0
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        while todo or running:
            while todo and len(running) < jobs and time.time() - t_last >= STAGGER_S[stage]:
                chunk, todo = todo[:BATCH[stage]], todo[BATCH[stage]:]
                rdir = Path(tmp) / f"w{t_last:.0f}_{chunk[0].stem}"
                rdir.mkdir()
                cmd = [sys.executable, str(Path(__file__).resolve()), "_batch", stage, str(rdir)] + [str(f) for f in chunk] \
                    + (["--keep"] if keep else [])
                log = open(rdir / "worker.log", "w", encoding="utf-8", errors="replace")  # noqa: SIM115 (closed when the worker ends)
                p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT), env=dict(os.environ, PYTHONUTF8="1"))
                running.append(dict(p=p, files=list(chunk), rdir=rdir, t=time.time(), log=log))
                t_last = time.time()
            for w in list(running):
                rc = w["p"].poll()
                for f in list(w["files"]):              # harvest finished models as they come
                    res = w["rdir"] / f"{f.stem}.json"
                    if res.exists():
                        try:
                            rec = json.loads(res.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):   # being written, or held by a scanner: next poll
                            continue
                        w["files"].remove(f); w["t"] = time.time()
                        done += 1; data[f.name] = rec; _save(path, data); _print(done, f, rec)
                if rc is None and time.time() - w["t"] < TIMEOUT[stage]:
                    continue
                if w["files"]:                          # the model being run when the worker died or hung
                    f = w["files"][0]
                    if rc is None:
                        _kill(w["p"])
                        rec = dict(file=f.name, ok=False, stage="timeout", error_type="Timeout",
                                   error=f"no result after {TIMEOUT[stage]} s", seconds=TIMEOUT[stage])
                    else:
                        w["log"].flush()
                        tail = (w["rdir"] / "worker.log").read_text(encoding="utf-8", errors="replace")[-1500:]
                        rec = dict(file=f.name, ok=False, stage="crash", error_type=f"exit {rc}", error=tail,
                                   seconds=round(time.time() - w["t"], 1))
                    done += 1; data[f.name] = rec; _save(path, data); _print(done, f, rec)
                    todo = w["files"][1:] + todo        # the rest of the chunk is run again
                w["log"].close()
                running.remove(w)
            time.sleep(0.2)
    return data


def _print(done, f, rec):
    msg = "ok" if rec.get("ok") else f"FAILED at {rec.get('stage')}: {rec.get('error_type')}: {str(rec.get('error'))[:160]}"
    print(f"[{done}] {f.name}: {msg} ({rec.get('seconds')} s)", flush=True)


def _kill(p):
    if os.name == "nt":                            # also kills EnergyPlus children
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, check=False)
    else:
        p.kill()


def worker(stage: str, rdir: str, idfs: list[str], keep: bool) -> None:
    """Run models one after another in this process; one result file per model (written atomically)."""
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for idf in idfs:
        rec = topology_one(Path(idf)) if stage == "topology" else scene_one(Path(idf), keep)
        tmp = Path(rdir) / f"{Path(idf).stem}.part"
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        tmp.replace(Path(rdir) / f"{Path(idf).stem}.json")


# ---------------------------------------------------------------- report
TOPOLOGY_NOTES = [
    "### What was fixed, and what the remaining mismatches are", "",
    ("Tracer and loader bugs found by this survey and fixed (2026-11-19), none by name: (1) object types written in "
    "fields are case-insensitive in EnergyPlus (`AirloopHVAC:UnitarySystem` in a Branch); `EPModel.objs/obj` and the "
    "tracer now match them so, and index names (a 130-zone model took minutes in the reference walk); (2) zone "
    "equipment without an air inlet node (baseboards, radiant and cooling panels, high-temperature radiant heaters, "
    "zone exhaust fans) is linked to its zone through the zone's equipment list; (3) node fields whose names do not say "
    "inlet/outlet get their direction from the field description (ideal loads, ventilated slab, unit ventilator, hybrid "
    "unitary, zone evaporative cooler, outdoor-air unit, heat pump water heater, the bypass duct of a changeover-bypass "
    "VAV unit); (4) declared equipment that names no node of its own (energy recovery ventilator, heat-exchanger-"
    "assisted coil systems, desiccant dehumidifier, electric baseboards) becomes a component carrying its branch's and "
    "its parts' ports; (5) the rest of every air loop the trace passes (relief fan, exhaust side of a run-around or "
    "heat-recovery loop) and the parts a chain component declares (a heat pump water heater's tank and fan) are added "
    "and traced upstream in turn; (6) `<x>_object_type` pairs spelt `<x>_object_name` (VRF terminals) or `<y>_<x>_name` "
    "(window air conditioner) are read; (7) performance-data and airflow-network objects (`CoilPerformance:*`, "
    "`UnitarySystemPerformance:*`, `AirflowNetwork:*`, ...) are not components. The reference was corrected in the same "
    "ways where it read declarations too narrowly (case of object types, NodeList splitter outlets, the pair spellings, "
    "a zone outdoor-air unit's equipment list, and the plant loop of every demand-side component, not only of those "
    "with 'water' in a field name)."), "",
    ("Remaining differences, not tracer errors: parts that a declared object names without an object-type field (the "
    "fan and mixer of fan-powered terminals, the heat exchanger and fans of energy recovery ventilators, the fans of "
    "outdoor-air units) are found by the tracer and not by the reference; the reference does not follow "
    "`AirLoopHVAC:DedicatedOutdoorAirSystem`, which feeds several air loops through `AirLoopHVAC:Mixer/Splitter`; the "
    "tracer includes what is upstream of a supply-side component (the service-water pump ahead of a heat pump water "
    "heater's tank, the return plenum an ideal-loads system draws from); `SolarCollector:UnglazedTranspired:"
    "Multisystem` names its collector, not the reverse."), ""]


SCENE_NOTES = [
    ("Notes. Pipeline bugs found by this stage and fixed (2026-11-19): simplified rectangular surfaces (`Wall:Exterior`, "
    "`Roof`, `Floor:Adiabatic`, `*:Interzone`, ...) and the interzone sub-surfaces were not read; EnergyPlus overrides "
    "the requested time step for Conduction Finite Difference models (20 per hour) and the time axis came out empty; "
    "a model with one time step per hour reports HVAC sub-steps only (auto mode now uses at least 4 per hour); scene "
    "paths longer than 260 characters made the USD write fail on Windows; an incomplete simulation cache (results "
    "without `eplusout.err`) was re-used; plenums across an interzone ceiling whose boundary names a zone were not "
    "found. The 8 'model as shipped fails' files also fail when the unmodified example file is run by EnergyPlus 22.2 "
    "(four need the Slab/Basement ground preprocessors that EP-Launch runs to create their other-side coefficients, two "
    "have inconsistent lookup tables, one has node-connection errors and one a Python plugin that aborts the run; two of "
    "them are EMS/plugin 'kill' tests). DElight models write scratch files into the model "
    "folder (EnergyPlus runs there so relative file names resolve), so two of them collided when run in parallel, and "
    "two supermarket models ran out of memory with six parallel workers; all four were re-run one at a time and built. "
    "Five models whose zone has no Floor or Ceiling surface of its own (a ground floor over a Kiva or basement "
    "foundation, an underground pipe test) were built without a camera, because the indoor candidate grid of "
    "`render/cameras.auto_pose` was the plan of the space's floors and ceilings; it now falls back to the plan of its "
    "roofs, then to the outline of all its surfaces, and the five were re-run and rendered."), ""]


def _pct(a, b):
    return f"{a / b:.3f}" if b else "—"


def _esc(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _short_error(r) -> str:
    """One line per failure for the taxonomy: exception type + the first informative line."""
    err = str(r.get("error") or "")
    lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
    sev = [ln for ln in lines if "** Severe  **" in ln or "**  Fatal  **" in ln]
    line = (sev[0] if sev else lines[0] if lines else "")
    line = line.replace("** Severe  **", "").replace("**  Fatal  **", "").strip()
    return f"{r.get('error_type')}: {line[:140]}"


def topology_section(topo: dict) -> list[str]:
    recs = list(topo.values())
    ok = [r for r in recs if r["ok"]]
    scope = [r for r in recs if r.get("out_of_scope")]
    tot = lambda k, rs=ok: sum(r.get(k, 0) for r in rs)
    TP, FP, FN = tot("tp"), tot("fp"), tot("fn")
    in_scope = [r for r in recs if not r.get("out_of_scope")]
    L = ["## Stage 1: topology (no simulation)", "",
         (f"**{len(recs)} example files; {len(ok)} loaded and traced ({len([r for r in in_scope if r['ok']])} of "
         f"{len(in_scope)} in scope); {tot('zones')} zones, {tot('conditioned')} conditioned; {tot('traced')} of "
         f"{tot('conditioned')} conditioned zones traced; component-level precision {_pct(TP, TP + FP)} and recall "
         f"{_pct(TP, TP + FN)} against the declared structure ({TP} of {TP + FN} declared zone-component pairs found, "
         f"{FP} found but not declared); {tot('exact')} of {tot('conditioned')} zones exact.** "
         f"{len(scope)} files are out of scope (below)."), "",
         ("*Traced*: a non-empty serving chain was recovered for a conditioned zone (a zone with "
         "`ZoneHVAC:EquipmentConnections`). *Precision / recall / exact*: as in `docs/topology_survey.md` (same reference "
         "`declared_chain`, built only from the objects that declare the system structure). System types are derived "
         "from object types, never from names."), ""]
    # per system type (zone level: the object types of each zone's declared and traced chain)
    agg = defaultdict(lambda: Counter())
    for r in ok:
        for z in r["zone_results"]:
            for t in z["tags"]:
                a = agg[t]
                a["zones"] += 1; a["traced"] += z["traced"]; a["tp"] += z["tp"]; a["fp"] += z["fp"]; a["fn"] += z["fn"]
                a["exact"] += z["exact"]
        for t in {t for z in r["zone_results"] for t in z["tags"]}:
            agg[t]["models"] += 1
    L += ["### Per system type (conditioned zones, by the object types in each zone's chain)", "",
          "| System type | Models | Conditioned zones | Traced | Precision | Recall | Exact |", "|---|---|---|---|---|---|---|"]
    for t, a in sorted(agg.items(), key=lambda kv: -kv[1]["zones"]):
        L.append(f"| {t} | {a['models']} | {a['zones']} | {a['traced']} | {_pct(a['tp'], a['tp'] + a['fp'])} | "
                 f"{_pct(a['tp'], a['tp'] + a['fn'])} | {a['exact']} |")
    # mismatches by object type
    fp_t, fn_t = Counter(), Counter()
    for r in ok:
        for z in r["zone_results"]:
            for k in z["fp_items"]:
                fp_t[_type_of(k)] += 1
            for k in z["fn_items"]:
                fn_t[_type_of(k)] += 1
    untraced = [(r["file"], z["zone"]) for r in ok for z in r["zone_results"] if not z["traced"]]
    named = sum(z.get("fp_named", 0) for r in ok for z in r["zone_results"])
    L += ["", "### Mismatches by object type (zone-component pairs)", "",
          (f"Of the {FP} traced-but-not-declared pairs, {named} are parts that a declared object names in a `<x>_name` "
          "field without an object-type field (for example the fan and mixer of a fan-powered terminal, or the heat "
          "exchanger and fans of an energy recovery ventilator); the reference does not follow such references, the "
          "tracer reaches them through their nodes."), "",
          "| Object type | Declared, not traced (FN) | Traced, not declared (FP) |", "|---|---|---|"]
    for t in sorted(set(fp_t) | set(fn_t), key=lambda t: -(fp_t[t] + fn_t[t])):
        L.append(f"| {t} | {fn_t[t]} | {fp_t[t]} |")
    L += ["", f"Conditioned zones without a traced chain: {len(untraced)}"
          + (" (" + "; ".join(f"{f}: {z}" for f, z in untraced[:30]) + (" ..." if len(untraced) > 30 else "") + ")" if untraced else ""), ""]
    # models with mismatches other than named parts
    rows = []
    for r in ok:
        zs = [z for z in r["zone_results"] if z["fn"] or z["fp"] > z.get("fp_named", 0)]
        if not zs:
            continue
        fn = Counter(_type_of(k) for z in zs for k in z["fn_items"])
        fp = Counter(_type_of(k) for z in zs for k in z["fp_items"])
        rows.append((r["file"], len(zs), r["conditioned"], fn, fp))
    if rows:
        L += ["### Models with other mismatches", "",
              ("Zones whose chain differs from the reference in more than named parts (object types with zone counts; FP "
              "lists include named parts):"), "",
              "| Model | Zones | Declared, not traced | Traced, not declared |", "|---|---|---|---|"]
        for f, nz, nc, fn, fp in sorted(rows):
            L.append(f"| {f} | {nz}/{nc} | {', '.join(f'{k} ({v})' for k, v in fn.most_common(6)) or '—'} | "
                     f"{', '.join(f'{k} ({v})' for k, v in fp.most_common(6)) or '—'} |")
        L.append("")
    L += TOPOLOGY_NOTES
    # failure taxonomy
    fails = [r for r in recs if not r["ok"]]
    L += ["### Failures and out-of-scope files", "", "| Reason | Files | Examples |", "|---|---|---|"]
    groups = defaultdict(list)
    for r in recs:
        if r.get("out_of_scope"):
            groups["out of scope: " + r["out_of_scope"]].append(r["file"])
        elif not r["ok"]:
            groups[f"{r.get('stage')}: {_short_error(r)}"].append(r["file"])
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        L.append(f"| {_esc(k)} | {len(v)} | {', '.join(v[:6])}{' ...' if len(v) > 6 else ''} |")
    if not fails:
        L.append("| (no failures) | 0 | |")
    # per model
    L += ["", "### Per model", "",
          "| Model | Zones (conditioned) | HVAC components | Traced | Precision / recall | Exact | System types | Seconds |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(recs, key=lambda r: r["file"].lower()):
        if not r["ok"]:
            L.append(f"| {r['file']} | {_esc('failed at ' + str(r.get('stage')) + ': ' + _short_error(r))} | | | | | "
                     f"{', '.join(r.get('tags', []))} | {r.get('seconds', '')} |")
            continue
        L.append(f"| {r['file']} | {r['zones']} ({r['conditioned']}) | {r['components']} | {r['traced']}/{r['conditioned']} | "
                 f"{_pct(r['tp'], r['tp'] + r['fp'])} / {_pct(r['tp'], r['tp'] + r['fn'])} | {r['exact']}/{r['conditioned']} | "
                 f"{', '.join(r['tags']) or 'none'} | {r['seconds']} |")
    return L


def _type_of(key: str) -> str:
    """Object type of a component key 'TYPE:NAME' (types contain ':', names may too): longest known type prefix."""
    parts = key.split(":")
    for i in range(len(parts) - 1, 0, -1):
        t = ":".join(parts[:i])
        if t in _KNOWN_TYPES:
            return _KNOWN_TYPES[t]
    return parts[0]


_KNOWN_TYPES: dict[str, str] = {}


def _known_types():
    """Upper-case -> canonical object type names from the EnergyPlus IDD (for readable mismatch tables)."""
    from thermoscene.energyplus.runner import energyplus_dir, find_energyplus
    try:
        idd = (energyplus_dir(find_energyplus()) / "Energy+.idd").read_text(encoding="latin-1")
    except (OSError, FileNotFoundError):
        return
    for line in idd.splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(",") and not line.startswith(("\\", "!")):
            t = line.strip().rstrip(",")
            _KNOWN_TYPES[t.upper()] = t


def _scene_failure(r) -> tuple[str, str]:
    """(category, reason) of a failed scene build."""
    if r.get("out_of_scope"):
        return "out of scope", r["out_of_scope"]
    un = r.get("unmodified") or {}
    if r.get("stage") == "simulate" and un.get("fails"):
        lines = [ln.strip() for ln in un.get("error", "").splitlines() if "Severe" in ln or "Fatal" in ln]
        return "EnergyPlus (model as shipped fails)", (lines[0] if lines else "fatal error")[:140]
    return str(r.get("stage")), _short_error(r)


def scene_section(sc: dict) -> list[str]:
    recs = list(sc.values())
    ok = [r for r in recs if r["ok"]]
    rendered = [r for r in ok if r.get("renders")]
    in_scope = [r for r in recs if not r.get("out_of_scope")]
    runnable = [r for r in in_scope if _scene_failure(r)[0] != "EnergyPlus (model as shipped fails)" or r["ok"]]
    secs = sorted(r["build_seconds"] for r in ok)
    tot = sorted(r["seconds"] for r in recs if r.get("seconds"))
    L = ["## Stage 2: scenes (auto mode, with simulation and rendering)", "",
         f"**{len(recs)} example files; {len(ok)} scenes built and {len(rendered)} rendered: {len(ok)} of {len(in_scope)} "
         f"in scope, {len(ok)} of {len(runnable)} that EnergyPlus 22.2 runs as shipped.** Each scene: "
         "`python -m thermoscene auto <model> --render` (weather file nearest to Site:Location, run periods 14-15 Jan "
         "and 15-16 Jul, default zone = conditioned zone with the longest traced chain, automatic cameras), one preview "
         "frame per camera. A simulation failure is checked by running the unmodified example file (design days, same "
         "weather file); if that fails too, the failure is EnergyPlus's own."
         + (f" Scene build after the simulation: median {secs[len(secs) // 2]:.0f} s, max {secs[-1]:.0f} s; whole run "
            f"per model (simulation, build, rendering): median {tot[len(tot) // 2]:.0f} s, total {sum(tot) / 3600:.1f} h "
            "of worker time." if secs and tot else ""), ""]
    if ok:
        c = Counter()
        for r in ok:
            c["surfaces"] += r["counts"]["surfaces"]; c["elements"] += r["counts"]["elements"]; c["components"] += r["counts"]["components"]
        withp = sum(1 for r in ok if r.get("plenum"))
        with_el = sum(1 for r in ok if r["counts"]["elements"])
        cams = Counter(n for r in ok for n in r.get("cameras", []))
        ecls = Counter()
        for r in ok:
            ecls.update(r.get("element_classes", {}))
        L += [f"Totals over built scenes: {c['surfaces']} surfaces, {c['elements']} generated elements, {c['components']} "
              f"simulated components; {withp} scenes with a plenum, {with_el} with generated HVAC elements. Cameras: "
              + ", ".join(f"{k} {v}" for k, v in cams.most_common()) + ". Element classes: "
              + ", ".join(f"{k} {v}" for k, v in ecls.most_common()) + ".", ""]
    L += ["### Failure taxonomy", "", "| Stage | Reason | Files | Examples |", "|---|---|---|---|"]
    groups = defaultdict(list)
    for r in recs:
        if not r["ok"]:
            groups[_scene_failure(r)].append(r["file"])
    for (st, why), v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        L.append(f"| {st} | {_esc(why)} | {len(v)} | {', '.join(v[:5])}{' ...' if len(v) > 5 else ''} |")
    by_stage = Counter(_scene_failure(r)[0] for r in recs if not r["ok"])
    no_cam = sorted(r["file"] for r in ok if not r.get("cameras"))
    L += ["", "Failures by stage: " + (", ".join(f"{k} {v}" for k, v in by_stage.most_common()) or "none") + ".", ""]
    L += SCENE_NOTES + ([f"Built without a camera ({len(no_cam)}): {', '.join(no_cam)}.", ""] if no_cam else [])
    L += [
          "### Per model", "",
          "| Model | Result | Zone | Weather | Surfaces / elements / components | Plenum | Cameras | E+ warnings / severe | Build s |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(recs, key=lambda r: r["file"].lower()):
        ep = r.get("energyplus") or {}
        if r["ok"]:
            L.append(f"| {r['file']} | built | {_esc(r['zone'])} | {r['epw'][:22]} | {r['counts']['surfaces']} / "
                     f"{r['counts']['elements']} / {r['counts']['components']} | {'yes' if r.get('plenum') else 'no'} | "
                     f"{len(r.get('cameras', []))} | {ep.get('warnings', '')} / {ep.get('severe', '')} | {r['build_seconds']} |")
        else:
            cat, why = _scene_failure(r)
            L.append(f"| {r['file']} | {_esc(cat + ': ' + why)} | {_esc(r.get('zone', ''))} | {Path(str(r.get('epw', ''))).name[:22]} "
                     f"| | | | {ep.get('warnings', '')} / {ep.get('severe', '')} | |")
    return L


def report() -> None:
    _known_types()
    topo, sc = _load(TOPO_JSON), _load(SCENE_JSON)
    L = ["# Corpus survey: every EnergyPlus example file", "",
         ("Generated by `python case_studies/corpus_survey.py` (stages `topology`, `scenes`, `report`) on the "
         f"{len(topo) or len(sc)} `ExampleFiles/*.idf` of the EnergyPlus installation thermoscene runs with. Nothing is "
         "configured per model: the same code, defaults and automatic choices (`thermoscene/auto.py`) apply to every file. "
         "Raw results: `case_studies/corpus_survey.json` (topology), `case_studies/corpus_scenes.json` (scenes)."), ""]
    if topo:
        L += topology_section(topo) + [""]
    if sc:
        L += scene_section(sc) + [""]
    REPORT.write_text("\n".join(L), encoding="utf-8")
    print("wrote", REPORT)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_batch":
        return worker(sys.argv[2], sys.argv[3], [a for a in sys.argv[4:] if a != "--keep"], "--keep" in sys.argv)
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":         # one model (kept for drivers started before _batch)
        import logging
        logging.basicConfig(level=logging.WARNING)
        rec = topology_one(Path(sys.argv[3])) if sys.argv[2] == "topology" else scene_one(Path(sys.argv[3]), "--keep" in sys.argv)
        return Path(sys.argv[4]).write_text(json.dumps(rec), encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["topology", "scenes", "report"])
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", nargs="*", help="file name patterns (glob or substring)")
    ap.add_argument("--redo", action="store_true", help="re-run models already recorded")
    ap.add_argument("--failed", action="store_true", help="re-run models recorded as failed")
    ap.add_argument("--keep", action="store_true", help="scenes: keep USD, renders and simulation results")
    a = ap.parse_args()
    if a.stage != "report":
        drive(a.stage, a.jobs, a.only, a.redo, a.failed, a.keep)
    report()


if __name__ == "__main__":
    main()
