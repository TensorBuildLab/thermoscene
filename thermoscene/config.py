"""Load a case configuration: defaults.yaml deep-merged with the case file; paths made absolute."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .energyplus.runner import energyplus_dir, find_energyplus

DEFAULTS = Path(__file__).with_name("defaults.yaml")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(case_path) -> dict:
    case_path = Path(case_path).resolve()
    with open(DEFAULTS) as f:
        cfg = yaml.safe_load(f)
    with open(case_path) as f:
        cfg = deep_merge(cfg, yaml.safe_load(f))
    exe = find_energyplus(cfg["energyplus"].get("executable"))
    cfg["energyplus"]["executable"] = exe
    subs = {"${ENERGYPLUS_DIR}": str(energyplus_dir(exe)), "${CASE_DIR}": str(case_path.parent)}

    def resolve(p):
        if p is None:
            return None
        for k, v in subs.items():
            p = p.replace(k, v)
        p = Path(p)
        return str(p if p.is_absolute() else (case_path.parent / p).resolve())

    for key in ("idf", "epw"):
        cfg["model"][key] = resolve(cfg["model"][key])
    cfg["output_dir"] = resolve(cfg.get("output_dir", "output"))
    cfg["case_dir"] = str(case_path.parent)
    cfg.setdefault("name", case_path.parent.name)
    if not cfg["scene"].get("zone"):
        raise ValueError("scene.zone is required")
    return cfg
