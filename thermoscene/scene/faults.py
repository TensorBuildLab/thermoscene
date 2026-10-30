"""Fault injection.

Envelope defects are injected into the EnergyPlus model BEFORE simulation, as an opaque sub-surface
(Door) on the host surface whose construction is the host construction with its insulation layer
replaced by a cavity. EnergyPlus then computes the defect's temperatures like any other surface.
Element faults (missing duct insulation) are applied by the generators; EnergyPlus fault objects
(FaultModel:*) are passed through verbatim.
"""
from __future__ import annotations

import numpy as np

from .geometry import frame, to2d

DEFECT_PREFIX = "TS_DEFECT_"


def envelope_defect(model, fault: dict, defaults: dict | None = None) -> tuple[list[str], str]:
    fault = {"mode": "cavity", **(defaults or {}), **fault}           # cavity_resistance: defaults.yaml
    host = model.surface(fault["surface"])
    if host is None:
        raise KeyError(f"fault: surface {fault['surface']} not found")
    o, u, v, n = frame(host["verts"])
    uv = to2d(host["verts"], o, u, v)
    ll = o + uv[:, 0].min() * u + uv[:, 1].min() * v                      # lower-left corner seen from outside
    u0, u1, v0, v1 = fault["rect"]
    world = np.array([ll + u0 * u + v1 * v, ll + u0 * u + v0 * v, ll + u1 * u + v0 * v, ll + u1 * u + v1 * v])
    pts = model.to_zone_coords(world, host["zone"])
    name = fault.get("name") or f"{DEFECT_PREFIX}{host['name']}"
    if not name.upper().startswith(DEFECT_PREFIX):
        name = DEFECT_PREFIX + name
    layers = model.layers(host["construction"])
    k = model.insulation_layer(host["construction"])
    if k is None:
        raise ValueError(f"fault: construction {host['construction']} has no insulation layer to remove")
    cav = f"{name}_Cavity"
    objs = []
    if fault["mode"] == "cavity":                  # otherwise the insulation layer is simply removed
        r_cav = float(fault["cavity_resistance"])
        # EnergyPlus accepts Material:AirGap only between two layers; an exposed cavity is written as a no-mass layer
        objs.append(f"Material:AirGap, {cav}, {r_cav};" if 0 < k < len(layers) - 1
                    else f"Material:NoMass, {cav}, Rough, {r_cav}, 0.9, 0.7, 0.7;")
    new_layers = layers[:k] + ([cav] if fault["mode"] == "cavity" else []) + layers[k + 1:]
    objs += [f"Construction, {name}_Construction, {', '.join(new_layers)};",
            f"FenestrationSurface:Detailed, {name}, Door, {name}_Construction, {host['name']}, , autocalculate, , 1, 4, "
            + ", ".join(f"{x:.4f}, {y:.4f}, {z:.4f}" for x, y, z in pts) + ";"]
    return objs, name


def injection_objects(model, faults: list[dict], defaults: dict | None = None) -> tuple[list[str], set[str]]:
    objs, defect_names = [], set()
    for f in faults or []:
        if f["type"] == "envelope_defect":
            o, nm = envelope_defect(model, f, (defaults or {}).get("envelope_defect"))
            objs += o; defect_names.add(nm.upper())
        elif f["type"] == "energyplus_object":
            objs.append(f["idf"])
    return objs, defect_names
