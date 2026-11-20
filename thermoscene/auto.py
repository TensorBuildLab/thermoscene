"""Auto mode: build thermal scenes from any EnergyPlus model without writing a case file.

    python -m thermoscene auto <model.idf> [--epw FILE] [--zone NAME|largest|all] [--out DIR] [--render]

Every choice below is generic (object types, geometry, the traced topology; never object names) and its parameters
are in `defaults.yaml` under `auto`:

* weather: the EPW in the EnergyPlus `WeatherData` folder nearest (great circle) to the model's `Site:Location`;
  `auto.fallback_epw` if the model has no location;
* run periods `auto.run_periods` (14-15 January and 15-16 July, starting on a Wednesday, as the paper's cases); the
  model's own time step is kept;
* zone: the conditioned zone with the longest traced serving chain, ties broken by floor area; the largest zone if no
  zone is conditioned. `largest`: the largest conditioned zone; `all`: every conditioned zone (every zone if none is);
* plenum `auto`; rooftop equipment on the model's roof surfaces if the scene has any, else none; exterior skins and
  faults as in the defaults (no faults);
* cameras (`cameras: auto`, resolved by the pipeline once the scene exists): a zone camera on a diffuser, else a
  window, else the largest wall; a plenum camera on the terminal box, else a duct, if there is a plenum; a rooftop
  camera on the largest rooftop element, if any. A target that no candidate viewpoint sees falls back to the next one.

The configuration is written next to the outputs (`auto_case.yaml`, cameras resolved after the build), so
`python -m thermoscene build <out>/auto_case.yaml` reproduces the scene.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import tempfile
from pathlib import Path

import numpy as np
import yaml

from .config import DEFAULTS
from .energyplus.runner import energyplus_dir, find_energyplus

log = logging.getLogger("thermoscene.auto")


def auto_defaults() -> dict:
    with open(DEFAULTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["auto"]


# ---------------------------------------------------------------- weather
def site_location(model) -> tuple[float, float] | None:
    loc = next(iter(model.objs("Site:Location").values()), None)
    try:
        return (float(loc["latitude"]), float(loc["longitude"])) if loc else None
    except (KeyError, TypeError, ValueError):
        return None


def epw_location(path) -> tuple[float, float] | None:
    """(latitude, longitude) from the LOCATION header line of an EPW file."""
    try:
        with open(path, encoding="latin-1") as f:
            h = f.readline().split(",")
        return float(h[6]), float(h[7])
    except (OSError, IndexError, ValueError):
        return None


def great_circle_km(a, b) -> float:
    (la1, lo1), (la2, lo2) = (np.radians(a), np.radians(b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def nearest_epw(location, weather_files, fallback: str | None = None) -> Path:
    """The weather file nearest to `location` (lat, lon); `fallback` (a file name among them) without a location."""
    files = [Path(f) for f in weather_files]
    if not files:
        raise FileNotFoundError("no EPW weather files found; pass --epw")
    if location is not None:
        located = [(great_circle_km(location, loc), f.name, f) for f in files if (loc := epw_location(f)) is not None]
        if located:
            return min(located)[2]
    named = [f for f in files if fallback and f.name.lower() == fallback.lower()]
    return named[0] if named else min(files)


# ---------------------------------------------------------------- zones
def polygon_area(pts) -> float:
    p = np.asarray(pts, float)
    return 0.5 * float(np.linalg.norm(np.sum(np.cross(p, np.roll(p, -1, axis=0)), axis=0)))


def floor_area(model, zone: str) -> float:
    """Floor area of one zone instance from its surfaces (floors; else the horizontal projection of its roofs)."""
    srf = model.surfaces(zone)
    floors = [s for s in srf if s["type"] == "Floor"]
    if floors:
        return sum(polygon_area(s["verts"]) for s in floors)
    return sum(polygon_area(np.c_[s["verts"][:, :2], np.zeros(len(s["verts"]))]) for s in srf if s["type"] in ("Roof", "Ceiling"))


def zone_table(model, graph) -> list[dict]:
    """One row per zone with readable surfaces: conditioned?, traced chain length, floor area."""
    rows = []
    for z in model.zone_names():
        if not model.surfaces(z):
            continue
        chain = 0
        if z.upper() in graph.zone_inlets:
            try:
                chain = len(graph.serving(z)["components"])
            except KeyError:
                chain = 0
        rows.append(dict(zone=z, conditioned=z.upper() in graph.zone_inlets, chain=chain, area=round(floor_area(model, z), 3)))
    return rows


def choose_zones(rows: list[dict], spec: str | None = None) -> list[str]:
    """spec None/'auto': longest chain (ties: floor area); 'largest': largest floor area; 'all': every zone; else a
    zone name. Conditioned zones are preferred in every mode; without any, all zones are candidates."""
    if not rows:
        raise ValueError("the model has no zone with surfaces thermoscene can read")
    pool = [r for r in rows if r["conditioned"]] or rows
    if spec in (None, "", "auto"):
        return [max(pool, key=lambda r: (r["chain"], r["area"], r["zone"]))["zone"]]
    if spec == "largest":
        return [max(pool, key=lambda r: (r["area"], r["chain"], r["zone"]))["zone"]]
    if spec == "all":
        return [r["zone"] for r in pool]
    hit = [r["zone"] for r in rows if r["zone"].upper() == spec.upper()]
    if not hit:
        raise KeyError(f"zone {spec} not in model (zones with surfaces: {[r['zone'] for r in rows]})")
    return hit


# ---------------------------------------------------------------- configuration
def model_timesteps(model) -> int | None:
    ts = next(iter(model.objs("Timestep").values()), None)
    try:
        return int(ts["number_of_timesteps_per_hour"]) if ts else None
    except (KeyError, TypeError, ValueError):
        return None


def has_roof(model, zone: str, plenum: str | None) -> bool:
    """True if the scene will contain roof surfaces facing outdoors (they become the exterior roof skins)."""
    return any(s["type"] == "Roof" and s["boundary"].lower() == "outdoors"
               for z in [zone] + ([plenum] if plenum else []) for s in model.surfaces(z))


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "zone"


def case_config(idf, zone, epw, out_dir, cache_dir, model, A: dict, timesteps=None) -> dict:
    from .scene.spaces import find_plenum
    plenum = find_plenum(model, zone, "auto")
    return {
        "name": f"{safe_name(Path(idf).stem)[:A['name_chars'][0]]}_{safe_name(zone)[:A['name_chars'][1]]}",  # short paths (Windows)
        "model": {"idf": str(Path(idf).resolve()), "epw": str(Path(epw).resolve())},
        "output_dir": str(Path(out_dir).resolve()),
        "energyplus": {"run_periods": copy.deepcopy(A["run_periods"]), "timesteps_per_hour": timesteps,
                       "cache_dir": str(Path(cache_dir).resolve())},
        "scene": {"zone": zone, "plenum": "auto", "roof": {"source": "model" if has_roof(model, zone, plenum) else "none"}},
        "faults": [],
        "storage": {"time_series": A["storage"]},
        "cameras": "auto",
        "auto": {"frames": list(A["frames"])},
    }


def write_case(cfg: dict, path: Path, note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"# Generated by `thermoscene auto` ({note}).\n"
            f"# Reproduce with:  python -m thermoscene build {path.name}\n")
    path.write_text(head + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


# ---------------------------------------------------------------- cameras (called by the pipeline for `cameras: auto`)
def _bbox(faces):
    p = np.array([v for f in faces for v in f], float)
    return p.min(0), p.max(0)


def _size(thing) -> float:
    """Surface area of an element (sum over its faces): a casing beats a long thin pipe."""
    return sum(polygon_area(f) for f in thing.quads)


def pick_frames(labels, specs) -> list[str]:
    """The labels of `specs` ('ENV HH:MM'); a spec with no such time step falls back to the middle of its run period
    (or of the whole run)."""
    from .render.cameras import resolve_frames
    out = []
    for s in specs:
        try:
            out += resolve_frames([s], labels)
        except KeyError:
            env = str(s).split()[0].upper()
            same = [lab for lab in labels if lab.upper().startswith(env + " ")]
            out.append((same or labels)[len(same or labels) // 2])
    return list(dict.fromkeys(out))


def camera_targets(scene, ids) -> dict[str, list]:
    """Candidate targets per camera, best first (see the module docstring)."""
    from .ontology import Element, Surface
    els, srf = scene.of(Element), scene.of(Surface)
    area = lambda s: polygon_area(s.polygon)
    zone = ids["zone"]
    diffusers = [e for e in els if e.space == zone and e.cls == "Diffuser"]
    windows = sorted([s for s in srf if s.space == zone and s.cls == "Opening" and s.surface_type.lower() in ("window", "glassdoor")],
                     key=area, reverse=True)
    walls = sorted([s for s in srf if s.space == zone and s.cls == "Surface" and s.surface_type == "Wall"], key=area, reverse=True)
    out = {"zone": diffusers[:2] + windows[:2] + walls[:3]}
    if ids.get("plenum"):
        pl = ids["plenum"]
        boxes = [e for e in els if e.space == pl and e.cls == "TerminalBox"]
        ducts = sorted([e for e in els if e.space == pl and e.cls == "Duct"], key=_size, reverse=True)
        out["plenum"] = boxes + ducts[:3]
    roof = sorted([e for e in els if e.space == ids["outdoors"]], key=_size, reverse=True)
    if roof:
        out["outdoors"] = roof[:4]
    return out


def choose_cameras(scene, ids, cfg, labels, meshes_fn, raycast_fn) -> list[dict]:
    """Camera configurations (auto poses with concrete target ids) for a generated scene."""
    from .render.cameras import auto_pose
    from .usd.writer import faces_of
    A = cfg["auto"]
    C = A["cameras"]
    frames = pick_frames(labels, A["frames"])
    cache = {}

    def meshes():                                        # the scene does not change while cameras are chosen
        if "m" not in cache:
            cache["m"] = meshes_fn()
        return cache["m"]

    cams = []
    for space, targets in camera_targets(scene, ids).items():
        spec = C[space]
        for tgt in targets:
            base = dict(spec.get("auto", {}))
            if space == "outdoors":                       # ring radius grows with the target's size
                lo, hi = _bbox(faces_of(tgt))
                base["distance"] = round(max(base["distance"], spec["distance_per_size"] * float(np.max(hi - lo))), 2)
            elif "height" in base:                        # eye height above the space floor, inside low spaces too
                sp = scene.get(ids[space])
                base["height"] = round(min(base["height"], spec["max_height_frac"] * (sp.z_ceiling - sp.z_floor)), 2)
            chosen = None
            for relax in [{}] + list(spec.get("relax", [])):
                a = dict(base, **relax, target=tgt.id)
                cam = dict(name=spec["name"], space=space, hfov_deg=spec.get("hfov_deg", cfg["camera"]["hfov_deg"]), auto=a,
                           frames=frames)
                try:
                    auto_pose(scene, ids, dict(cam, resolution=cfg["camera"]["resolution"]), meshes, raycast_fn)
                    chosen = cam
                    break
                except (RuntimeError, ValueError) as e:
                    log.info("auto camera %s: %s", spec["name"], e)
            if chosen:
                cams.append(chosen)
                break
        else:
            log.warning("auto camera %s: no candidate target is visible; camera skipped", spec["name"])
    return cams


# ---------------------------------------------------------------- driver
def _tag(e: Exception, stage: str, **info) -> Exception:
    try:
        if not hasattr(e, "thermoscene_stage"):
            e.thermoscene_stage = stage
        e.thermoscene_info = {**getattr(e, "thermoscene_info", {}), **info}
    except AttributeError:
        pass
    return e


def run(idf, epw=None, zone=None, out_dir=None, render=False, preview_frames=None, cache_dir=None, exe=None) -> list[dict]:
    """Build one scene per chosen zone. Returns [{zone, epw, output_dir, usd, case}] (case = the auto_case.yaml); with
    several zones a failed zone is reported as {..., error} and the others are still built."""
    from . import pipeline
    from .energyplus.topology import HVACGraph
    A = auto_defaults()
    idf = Path(idf).resolve()
    out_dir = Path(out_dir or Path.cwd() / "auto_output" / idf.stem).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir or out_dir / ".sim_cache")
    if preview_frames:
        A["frames"] = A["frames"][:int(preview_frames)]
    try:
        exe = find_energyplus(exe)
        with tempfile.TemporaryDirectory() as tmp:
            model = pipeline.load_model(idf, exe, tmp)
    except Exception as e:  # noqa: BLE001
        raise _tag(e, "expand")
    try:
        graph = HVACGraph(model)
    except Exception as e:  # noqa: BLE001
        raise _tag(e, "topology")
    try:
        zones = choose_zones(zone_table(model, graph), zone)
    except Exception as e:  # noqa: BLE001
        raise _tag(e, "spaces")
    if epw is None:
        wdir = energyplus_dir(exe) / A["weather_dir"]
        epw = nearest_epw(site_location(model), sorted(wdir.glob("*.epw")), A["fallback_epw"])
    epw = Path(epw).resolve()
    timesteps = max(model_timesteps(model) or A["timesteps_per_hour"], A["min_timesteps_per_hour"])
    log.info("auto: %s, weather %s, zone(s) %s", idf.name, epw.name, zones)
    built = []
    for z in zones:
        zdir = out_dir / safe_name(z) if len(zones) > 1 else out_dir
        cfg = case_config(idf, z, epw, zdir, cache_dir, model, A, timesteps)
        case = zdir / "auto_case.yaml"
        write_case(cfg, case, "cameras chosen when the scene is built")
        try:
            usd = pipeline.build(case, render=render)
        except Exception as e:
            _tag(e, "auto", zone=z, epw=str(epw))
            if len(zones) == 1:
                raise
            log.error("zone %s failed at %s: %s", z, e.thermoscene_stage, e)     # --zone all: go on with the others
            built.append(dict(zone=z, epw=str(epw), output_dir=str(zdir), case=str(case),
                              error=f"{e.thermoscene_stage}: {type(e).__name__}: {e}"))
            continue
        summary = json.loads((zdir / "summary.json").read_text(encoding="utf-8"))
        cfg["cameras"] = summary.get("cameras", [])       # resolved: concrete target ids, same poses on a rebuild
        write_case(cfg, case, f"zone chosen by '{zone or 'auto'}'; cameras resolved from the generated scene")
        built.append(dict(zone=z, epw=str(epw), output_dir=str(zdir), usd=str(usd), case=str(case)))
    return built
