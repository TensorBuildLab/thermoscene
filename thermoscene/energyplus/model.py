"""Read an EnergyPlus model (epJSON, i.e. named fields) into geometry and constructions.

Handles GlobalGeometryRules (relative/world coordinates, clockwise/counter-clockwise entry),
zone origins and rotation, BuildingSurface:Detailed and Wall/RoofCeiling/Floor:Detailed,
FenestrationSurface:Detailed and the simple Window/Door/GlazedDoor objects.
"""
from __future__ import annotations

import json
import logging
import math

import numpy as np

log = logging.getLogger(__name__)

SURFACE_CLASSES = {"BuildingSurface:Detailed": None, "Wall:Detailed": "Wall",
                   "RoofCeiling:Detailed": "RoofCeiling", "Floor:Detailed": "Floor"}
UNSUPPORTED_SURFACES = ("Wall:Exterior", "Wall:Adiabatic", "Wall:Underground", "Wall:Interzone", "Roof",
                        "Ceiling:Adiabatic", "Ceiling:Interzone", "Floor:GroundContact", "Floor:Adiabatic", "Floor:Interzone")
SIMPLE_SUBSURFACES = {"Window": "Window", "Door": "Door", "GlazedDoor": "GlassDoor"}


def newell_normal(pts) -> np.ndarray:
    p = np.asarray(pts, float)
    q = np.roll(p, -1, axis=0)
    n = np.array([np.sum((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])),
                  np.sum((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])),
                  np.sum((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1]))])
    return n / (np.linalg.norm(n) or 1.0)


def plane_frame(pts):
    """Orthonormal frame (origin, u, v, n) for a planar polygon; u horizontal for vertical surfaces."""
    p = np.asarray(pts, float); n = newell_normal(p)
    up = np.array([0.0, 0.0, 1.0])
    u = np.cross(up, n) if abs(n[2]) < 0.95 else np.array([1.0, 0.0, 0.0])
    u /= np.linalg.norm(u); v = np.cross(n, u)
    return p[0], u, v, n


class EPModel:
    def __init__(self, epjson) -> None:
        if not isinstance(epjson, dict):
            with open(epjson, encoding="utf-8") as f:
                epjson = json.load(f)
        self.d = epjson
        r = next(iter(self.objs("GlobalGeometryRules").values()), {})
        self.relative = r.get("coordinate_system", "Relative").lower() == "relative"
        self.clockwise = r.get("vertex_entry_direction", "Counterclockwise").lower() == "clockwise"
        self.warnings: list[str] = []

    # ---------------------------------------------------------------- generic access
    def objs(self, cls: str) -> dict:
        return self.d.get(cls, {})

    def obj(self, cls: str, name: str):
        for k, v in self.objs(cls).items():
            if k.upper() == name.upper():
                return k, v
        return None, None

    def classes(self) -> list[str]:
        return list(self.d)

    # ---------------------------------------------------------------- zones
    def zone_names(self) -> list[str]:
        return list(self.objs("Zone"))

    def zone_multiplier(self, zone: str) -> int:
        _, z = self.obj("Zone", zone)
        return int(z.get("multiplier", 1)) if z else 1

    def _to_world(self, pts, zone: str):
        p = np.asarray(pts, float)
        if self.relative and zone:
            _, z = self.obj("Zone", zone)
            z = z or {}
            th = math.radians(float(z.get("direction_of_relative_north", 0.0) or 0.0))
            if th:
                c, s = math.cos(th), math.sin(th)
                p = np.c_[p[:, 0] * c + p[:, 1] * s, -p[:, 0] * s + p[:, 1] * c, p[:, 2]]
            p = p + [float(z.get("x_origin", 0) or 0), float(z.get("y_origin", 0) or 0), float(z.get("z_origin", 0) or 0)]
        if self.clockwise:
            p = p[::-1]
        return p

    def to_zone_coords(self, pts, zone: str):
        """Inverse of _to_world: world points -> coordinates as they must be written in this model."""
        p = np.asarray(pts, float)
        if self.clockwise:
            p = p[::-1]
        if self.relative and zone:
            _, z = self.obj("Zone", zone)
            z = z or {}
            p = p - [float(z.get("x_origin", 0) or 0), float(z.get("y_origin", 0) or 0), float(z.get("z_origin", 0) or 0)]
            th = math.radians(float(z.get("direction_of_relative_north", 0.0) or 0.0))
            if th:
                c, s = math.cos(th), math.sin(th)
                p = np.c_[p[:, 0] * c - p[:, 1] * s, p[:, 0] * s + p[:, 1] * c, p[:, 2]]
        return p

    # ---------------------------------------------------------------- surfaces
    def surfaces(self, zone: str | None = None) -> list[dict]:
        out = []
        for cls, fixed_type in SURFACE_CLASSES.items():
            for name, f in self.objs(cls).items():
                zn = f.get("zone_name", "")
                if zone and zn.upper() != zone.upper():
                    continue
                verts = [(v["vertex_x_coordinate"], v["vertex_y_coordinate"], v["vertex_z_coordinate"]) for v in f["vertices"]]
                bc = f.get("outside_boundary_condition", "")
                st = f.get("surface_type") or fixed_type
                if st == "RoofCeiling":
                    st = "Roof" if bc.lower() == "outdoors" else "Ceiling"
                out.append(dict(name=name, type=st.capitalize() if st else "", construction=f.get("construction_name", ""),
                                zone=zn, boundary=bc, boundary_object=f.get("outside_boundary_condition_object"),
                                verts=self._to_world(verts, zn)))
        for cls in UNSUPPORTED_SURFACES:
            if self.objs(cls):
                self.warnings.append(f"{cls} objects are not read (convert to BuildingSurface:Detailed)")
        return out

    def surface(self, name: str) -> dict | None:
        return next((s for s in self.surfaces() if s["name"].upper() == name.upper()), None)

    def subsurfaces(self, host: dict) -> list[dict]:
        out = []
        for name, f in self.objs("FenestrationSurface:Detailed").items():
            if f.get("building_surface_name", "").upper() != host["name"].upper():
                continue
            n = int(f.get("number_of_vertices", 4) or 4)
            verts = [(f[f"vertex_{i}_x_coordinate"], f[f"vertex_{i}_y_coordinate"], f[f"vertex_{i}_z_coordinate"])
                     for i in range(1, n + 1)]
            out.append(dict(name=name, type=f.get("surface_type", "Window"), construction=f.get("construction_name", ""),
                            verts=self._to_world(verts, host["zone"]), multiplier=int(f.get("multiplier", 1) or 1)))
        for cls, st in SIMPLE_SUBSURFACES.items():
            for name, f in self.objs(cls).items():
                if f.get("building_surface_name", "").upper() != host["name"].upper():
                    continue
                o, u, v, nrm = plane_frame(host["verts"])
                if abs(nrm[2]) > 0.95:
                    self.warnings.append(f"{cls} {name}: simple sub-surfaces on horizontal hosts are not supported")
                    continue
                hv = np.asarray(host["verts"]); uu, vv = (hv - o) @ u, (hv - o) @ v
                ll = o + uu.min() * u + vv.min() * v
                x0, z0 = float(f.get("starting_x_coordinate", 0)), float(f.get("starting_z_coordinate", 0))
                L, H = float(f["length"]), float(f["height"])
                pts = [ll + (x0) * u + (z0 + H) * v, ll + x0 * u + z0 * v, ll + (x0 + L) * u + z0 * v, ll + (x0 + L) * u + (z0 + H) * v]
                out.append(dict(name=name, type=st, construction=f.get("construction_name", ""), verts=np.array(pts),
                                multiplier=int(f.get("multiplier", 1) or 1)))
        return out

    # ---------------------------------------------------------------- constructions / materials
    def layers(self, construction: str) -> list[str]:
        _, c = self.obj("Construction", construction)
        if not c:
            return []
        keys = ["outside_layer"] + [f"layer_{i}" for i in range(2, 11)]
        return [c[k] for k in keys if c.get(k)]

    def material(self, name: str):
        for cls in ("Material", "Material:NoMass", "Material:AirGap", "WindowMaterial:SimpleGlazingSystem",
                    "WindowMaterial:Glazing", "WindowMaterial:Gas"):
            k, m = self.obj(cls, name)
            if m is not None:
                return cls, k, m
        return None, None, None

    def face_emissivity(self, construction: str, side: str = "inside", default: float = 0.9) -> float:
        lay = self.layers(construction)
        if not lay:
            return default
        cls, _, m = self.material(lay[-1] if side == "inside" else lay[0])
        if cls in ("Material", "Material:NoMass"):
            return float(m.get("thermal_absorptance", 0.9))
        if cls == "WindowMaterial:SimpleGlazingSystem":
            return 0.84
        if cls == "WindowMaterial:Glazing":
            key = "back_side_infrared_hemispherical_emissivity" if side == "inside" else "front_side_infrared_hemispherical_emissivity"
            return float(m.get(key, 0.84))
        return default

    def insulation_layer(self, construction: str) -> int | None:
        """Index of the layer with the highest thermal resistance (the 'insulation')."""
        best, best_r = None, 0.0
        for i, name in enumerate(self.layers(construction)):
            cls, _, m = self.material(name)
            if cls == "Material":
                r = float(m["thickness"]) / float(m["conductivity"])
            elif cls == "Material:NoMass":
                r = float(m["thermal_resistance"])
            else:
                continue
            if r > best_r:
                best, best_r = i, r
        return best
