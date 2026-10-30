"""Build Space and Surface instances from an EnergyPlus model for a chosen zone (+ plenum)."""
from __future__ import annotations

import logging
import re

import numpy as np

from ..energyplus.model import newell_normal
from ..ontology import Provenance, Space, Surface
from .faults import DEFECT_PREFIX
from .geometry import offset

log = logging.getLogger(__name__)


def sid(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def plenum_zones(model) -> set[str]:
    names = set()
    for cls in ("AirLoopHVAC:ReturnPlenum", "AirLoopHVAC:SupplyPlenum"):
        for _, f in model.objs(cls).items():
            names.add(f["zone_name"].upper())
    return names


def find_plenum(model, zone: str, setting) -> str | None:
    if setting in (None, "none", False):
        return None
    if setting not in ("auto", True):
        return model.obj("Zone", setting)[0]
    known = plenum_zones(model)
    for s in model.surfaces(zone):
        if s["type"] == "Ceiling" and s["boundary"].lower() == "surface" and s["boundary_object"]:
            adj = model.surface(s["boundary_object"])
            if adj and (adj["zone"].upper() in known or "PLENUM" in adj["zone"].upper()):
                return adj["zone"]
    return None


def build(scene, model, cfg) -> dict:
    zone = model.obj("Zone", cfg["scene"]["zone"])[0]
    if zone is None:
        raise KeyError(f"zone {cfg['scene']['zone']} not in model; zones: {model.zone_names()}")
    plenum = find_plenum(model, zone, cfg["scene"].get("plenum", "auto"))
    eps = cfg["scene"]["interzone_offset_m"]
    skins = cfg["scene"]["exterior_skins"]
    included = {zone.upper()} | ({plenum.upper()} if plenum else set())
    out = scene.add(Space(id="Outdoors", cls="Outdoors", name="Outdoors"))
    ids = {"zone": sid(zone), "plenum": sid(plenum) if plenum else None, "outdoors": out.id}

    for zname, cls in [(zone, "Zone")] + ([(plenum, "Plenum")] if plenum else []):
        srfs = model.surfaces(zname)
        zs = np.concatenate([s["verts"][:, 2] for s in srfs])
        sp = scene.add(Space(id=sid(zname), cls=cls, name=zname, sim_zone=zname,
                             multiplier=model.zone_multiplier(zname), z_floor=float(zs.min()), z_ceiling=float(zs.max())))
        sp.prov("geometry", Provenance.SIMULATED, "EnergyPlus zone surfaces")
        for s in srfs:
            n = newell_normal(s["verts"])
            subs = model.subsurfaces(s)
            poly, holes = s["verts"], [ss["verts"] for ss in subs]
            if s["boundary"].lower() == "surface" and s["boundary_object"]:
                adj = model.surface(s["boundary_object"])
                if adj and adj["zone"].upper() in included:          # pull coincident interzone faces apart
                    poly = np.array(offset(poly, -n, eps)); holes = [np.array(offset(h, -n, eps)) for h in holes]
            host = scene.add(Surface(
                id=f"{sp.id}/{sid(s['name'])}", cls="Surface", name=s["name"], space=sp.id, sim_surface=s["name"],
                polygon=[tuple(p) for p in poly], holes=[[tuple(p) for p in h] for h in holes],
                construction=s["construction"], boundary=s["boundary"], adjacent=s["boundary_object"],
                emissivity=model.face_emissivity(s["construction"], "inside"), surface_type=s["type"],
                attrs=dict(face="inside")))
            host.prov("geometry", Provenance.SIMULATED, "EnergyPlus surface vertices")
            for ss in subs:
                st = "Defect" if ss["name"].upper().startswith(DEFECT_PREFIX) else ss["type"]
                spoly = ss["verts"] if poly is s["verts"] else offset(ss["verts"], -n, eps)
                scene.add(Surface(
                    id=f"{sp.id}/{sid(ss['name'])}", cls="Opening", name=ss["name"], space=sp.id, sim_surface=ss["name"],
                    polygon=[tuple(p) for p in spoly], construction=ss["construction"], boundary=s["boundary"],
                    host=host.id, emissivity=model.face_emissivity(ss["construction"], "inside"), surface_type=st,
                    attrs=dict(face="inside")))
            if skins and s["boundary"].lower() == "outdoors":
                d = 2 * eps
                scene.add(Surface(
                    id=f"Outdoors/{sid(s['name'])}_ext", cls="Surface", name=s["name"] + " (exterior)", space="Outdoors",
                    sim_surface=s["name"], polygon=offset(s["verts"], n, d), holes=[offset(h, n, d) for h in holes],
                    construction=s["construction"], boundary=s["boundary"], surface_type=s["type"],
                    emissivity=model.face_emissivity(s["construction"], "outside"), attrs=dict(face="outside")))
                for ss in subs:
                    scene.add(Surface(
                        id=f"Outdoors/{sid(ss['name'])}_ext", cls="Opening", name=ss["name"] + " (exterior)",
                        space="Outdoors", sim_surface=ss["name"], polygon=offset(ss["verts"], n, d),
                        construction=ss["construction"], boundary=s["boundary"], host=f"Outdoors/{sid(s['name'])}_ext",
                        surface_type="Defect" if ss["name"].upper().startswith(DEFECT_PREFIX) else ss["type"],
                        emissivity=model.face_emissivity(ss["construction"], "outside"), attrs=dict(face="outside")))
    for w in model.warnings:
        log.warning(w)
    return ids
