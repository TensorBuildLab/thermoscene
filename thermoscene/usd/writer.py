"""Write a SceneGraph to OpenUSD.

Layout (mirrors the ontology):
  /World/<Space>                       Xform  semantic:class = Zone|Plenum|Outdoors, thermal:* environment series
  /World/<Space>/Surfaces/<name>       Mesh   EnergyPlus surfaces and openings (thermal:surfaceTemperature ...)
  /World/<Space>/Elements/<name>       Mesh   generated physical elements (ts:represents -> /World/Systems/...)
  /World/Systems/<component>           Scope  EnergyPlus HVAC components (data only; ts:feeds, ports, sizes)
  /World/Cameras/<name>                Camera thermal:environment -> /World/<Space>
Every quantity carries `<attr>:provenance` (level) and `<attr>:provenanceNote`.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, Vt

from ..ontology import CLASSES, Camera, Element, SimComponent, Space, Surface
from ..thermal.radiometry import BAND_UM
from ..scene.geometry import grid_split, triangulate

CMAP = colormaps["inferno"]
T = Sdf.ValueTypeNames


def pname(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return s if s and not s[0].isdigit() else "_" + s


def faces_of(thing):
    if isinstance(thing, Surface):
        return triangulate(thing.polygon, thing.holes)
    return thing.quads


def scene_meshes(scene):
    """(thing, faces) for every visible thing; shared by the USD writer and the camera sampler."""
    return [(t, faces_of(t)) for t in list(scene.of(Surface)) + list(scene.of(Element))]


class Writer:
    def __init__(self, scene, cfg, labels, step_minutes):
        self.scene, self.cfg, self.labels, self.n = scene, cfg, labels, len(labels)
        self.mode = cfg["storage"]["time_series"]
        self.sidecar: dict[str, np.ndarray] = {}
        self.step = step_minutes
        self.paths: dict[str, str] = {}

    # ---------------------------------------------------------------- low level
    def mesh(self, path, faces):
        m = UsdGeom.Mesh.Define(self.stage, path)
        pts, idx, counts, lookup = [], [], [], {}
        for f in faces:
            for v in f:
                k = tuple(round(float(c), 6) for c in v)
                if k not in lookup:
                    lookup[k] = len(pts); pts.append(k)
                idx.append(lookup[k])
            counts.append(len(f))
        m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
        m.CreateFaceVertexCountsAttr(counts); m.CreateFaceVertexIndicesAttr(idx)
        m.CreateDoubleSidedAttr(True)
        a = np.array(pts); m.CreateExtentAttr([Gf.Vec3f(*a.min(0)), Gf.Vec3f(*a.max(0))])
        UsdPhysics.CollisionAPI.Apply(m.GetPrim())
        return m

    def put(self, prim, name, typ, value):
        prim.CreateAttribute(name, typ, custom=True).Set(value)

    def series(self, prim, name, values, typ=T.Float):
        a = prim.CreateAttribute(name, typ, custom=True)
        values = np.asarray(values)
        if self.mode == "sidecar":
            key = f"{prim.GetPath()}|{name}"
            self.sidecar[key] = values.astype(bool if typ == T.Bool else np.float32)
            a.SetCustomDataByKey("thermal:sidecarKey", key)
            a.Set(bool(values[0]) if typ == T.Bool else float(values[0]))
        else:
            cast = bool if typ == T.Bool else float
            for i, v in enumerate(values):
                a.Set(cast(v), Usd.TimeCode(i))
        return a

    def provenance(self, prim, thing, attr_map):
        for q, (lvl, note) in thing.provenance.items():
            base = attr_map.get(q, f"ts:{q}")
            self.put(prim, f"{base}:provenance", T.String, lvl.value)
            if note:
                self.put(prim, f"{base}:provenanceNote", T.String, str(note))

    def classes(self, prim, cls):
        self.put(prim, "semantic:class", T.String, cls)
        for k in ("brick", "s223"):
            if CLASSES.get(cls, {}).get(k):
                self.put(prim, f"semantic:{k}", T.String, CLASSES[cls][k])

    def color_range(self, space_id):
        vals = [v for (o, q), v in self.scene.series.items()
                if q == "surfaceTemperature" and getattr(self.scene.things[o], "space", None) == space_id]
        if not vals:
            return 15.0, 35.0
        allv = np.concatenate([np.asarray(v, float) for v in vals])
        return float(np.nanpercentile(allv, 1)), float(np.nanpercentile(allv, 99))

    def colors(self, mesh, values, rng):
        col = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant)
        lo, hi = rng
        f = lambda t: Gf.Vec3f(*[float(c) for c in CMAP(np.clip((t - lo) / max(hi - lo, 1e-6), 0, 1))[:3]])
        if self.mode == "sidecar":
            col.Set([f(values[0])])
        else:
            for i, v in enumerate(values):
                col.Set([f(v)], Usd.TimeCode(i))

    # ---------------------------------------------------------------- main
    def write(self, path):
        path = Path(path)
        self.stage = st = Usd.Stage.CreateNew(str(path))
        UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(st, 1.0)
        st.SetStartTimeCode(0); st.SetEndTimeCode(self.n - 1); st.SetTimeCodesPerSecond(60 / self.step)
        world = UsdGeom.Xform.Define(st, "/World"); st.SetDefaultPrim(world.GetPrim())
        UsdPhysics.Scene.Define(st, "/World/PhysicsScene").CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        UsdLux.DomeLight.Define(st, "/World/DomeLight").CreateIntensityAttr(800)
        sc = self.scene

        for sp in sc.of(Space):
            p = UsdGeom.Xform.Define(st, f"/World/{pname(sp.id)}").GetPrim()
            self.paths[sp.id] = str(p.GetPath())
            self.classes(p, sp.cls)
            if sp.sim_zone:
                self.put(p, "ts:simZone", T.String, sp.sim_zone)
                self.put(p, "ts:zoneMultiplier", T.Int, int(sp.multiplier))
            for (o, q), v in sc.series.items():
                if o == sp.id:
                    self.series(p, f"thermal:{q}", v)
            self.provenance(p, sp, {q: f"thermal:{q}" for (o, q) in sc.series if o == sp.id})

        jet_targets = {e.attrs["ceiling_surface"] for e in sc.of(Element) if e.cls == "Diffuser"}
        ranges = {sp.id: self.color_range(sp.id) for sp in sc.of(Space)}
        for thing, faces in scene_meshes(sc):
            kind = "Surfaces" if isinstance(thing, Surface) else "Elements"
            path_ = f"/World/{pname(thing.space)}/{kind}/{pname(thing.name)}"
            if path_ in self.paths.values():
                path_ += "_" + pname(thing.id.split("/")[0])
            if isinstance(thing, Surface) and thing.id in jet_targets and self.mode == "timesamples":
                faces = grid_split(thing.polygon, thing.holes, 0.25)
            m = self.mesh(path_, faces); prim = m.GetPrim()
            self.paths[thing.id] = path_
            ts = sc.series[(thing.id, "surfaceTemperature")]
            self.series(prim, "thermal:surfaceTemperature", ts)
            if isinstance(thing, Surface):
                self.classes(prim, "Opening" if thing.cls == "Opening" else "Surface")
                self.put(prim, "ts:surfaceType", T.String, thing.surface_type)
                self.put(prim, "ts:simSurface", T.String, thing.sim_surface)
                self.put(prim, "ts:construction", T.String, thing.construction)
                self.put(prim, "ts:boundary", T.String, thing.boundary)
                self.put(prim, "ts:face", T.String, thing.attrs.get("face", "inside"))
                self.put(prim, "thermal:emissivity", T.Float, float(thing.emissivity))
            else:
                self.classes(prim, thing.cls)
                mat = self.cfg["materials"][thing.material]
                self.put(prim, "thermal:emissivity", T.Float, float(mat["emissivity"]))
                self.put(prim, "thermal:solarAbsorptance", T.Float, float(mat["solar_absorptance"]))
                self.put(prim, "mep:material", T.String, thing.material)
                self.put(prim, "mep:medium", T.String, thing.medium.value)
                self.put(prim, "mep:size", T.String, str(thing.attrs.get("size", "")))
                self.put(prim, "thermal:model", T.String, thing.thermal_model)
                if thing.stream_node:
                    self.put(prim, "mep:streamNode", T.String, thing.stream_node)
                if thing.attrs.get("fault"):
                    self.put(prim, "ts:fault", T.String, thing.attrs["fault"])
                for q, name in (("fluidTemperature", "mep:fluidTemperature"), ("fluidMassFlow", "mep:fluidMassFlow"),
                                ("jetStrength", "thermal:ceilingJetStrength"), ("jetLength", "thermal:ceilingJetDecayLength")):
                    if (thing.id, q) in sc.series:
                        self.series(prim, name, sc.series[(thing.id, q)])
                if (thing.id, "condensationRisk") in sc.series:
                    self.series(prim, "thermal:condensationRisk", sc.series[(thing.id, "condensationRisk")], T.Bool)
                if thing.cls == "Diffuser":
                    self.put(prim, "thermal:ceilingJetCenter", T.Float3, Gf.Vec3f(*thing.attrs["jet_center"]))
                    self.put(prim, "thermal:ceilingJetHalfSize", T.Float, float(thing.attrs["jet_half"]))
            self.provenance(prim, thing, {"surfaceTemperature": "thermal:surfaceTemperature", "fluidTemperature": "mep:fluidTemperature",
                                          "fluidMassFlow": "mep:fluidMassFlow", "geometry": "ts:geometry", "size": "mep:size",
                                          "material": "mep:material"})
            if isinstance(thing, Surface) and thing.id in jet_targets and self.mode == "timesamples":
                self._jet_colors(m, thing, ts, ranges[thing.space])
            else:
                self.colors(m, ts, ranges.get(thing.space, (15, 35)))

        # systems (data prims)
        UsdGeom.Scope.Define(st, "/World/Systems")
        for c in sc.of(SimComponent):
            p = UsdGeom.Scope.Define(st, f"/World/Systems/{pname(c.sim_type + '_' + c.name)}").GetPrim()
            self.paths[c.id] = str(p.GetPath())
            self.classes(p, c.cls)
            for k, v in (("ts:simType", c.sim_type), ("ts:simName", c.name), ("ts:role", c.role), ("ts:system", c.system)):
                self.put(p, k, T.String, v)
            self.put(p, "ts:ports", T.StringArray, [f"{x.direction}|{x.medium.value}|{x.node}|{x.name}" for x in c.ports])
            for d, v in c.sizes.items():
                self.put(p, f"ts:size:{pname(d)}", T.Float, float(v))
        # relationships
        for thing in list(sc.of(Element)):
            prim = st.GetPrimAtPath(self.paths[thing.id])
            prim.CreateRelationship("ts:represents").SetTargets([Sdf.Path(self.paths[r]) for r in thing.represents if r in self.paths])
            prim.CreateRelationship("ts:connectedTo").SetTargets([Sdf.Path(self.paths[r]) for r in thing.connected_to if r in self.paths])
            prim.CreateRelationship("ts:locatedIn").SetTargets([Sdf.Path(self.paths[thing.space])])
            if thing.cls == "Diffuser":
                prim.CreateRelationship("thermal:ceilingJetTarget").SetTargets([Sdf.Path(self.paths[thing.attrs["ceiling_surface"]])])
        for s in sc.of(Surface):
            prim = st.GetPrimAtPath(self.paths[s.id])
            prim.CreateRelationship("ts:locatedIn").SetTargets([Sdf.Path(self.paths[s.space])])
            if s.host and s.host in self.paths:
                prim.CreateRelationship("ts:hostedBy").SetTargets([Sdf.Path(self.paths[s.host])])
        for a, b in sc.feeds:
            if a in self.paths and b in self.paths:
                st.GetPrimAtPath(self.paths[a]).CreateRelationship("ts:feeds").AddTarget(Sdf.Path(self.paths[b]))
        # cameras
        UsdGeom.Scope.Define(st, "/World/Cameras")
        for cam in sc.of(Camera):
            self._camera(cam)
        meta = {"ts:ontology": "thermoscene/1.0", "thermal:timeCodeLabels": Vt.StringArray(self.labels),
                "thermal:timestepMinutes": self.step, "thermal:temperatureUnit": "degC",
                "ts:source": sc.meta.get("source", ""), "ts:case": self.cfg.get("name", ""), "thermal:storage": self.mode}
        if self.mode == "sidecar":
            side = path.with_suffix(".series.npz")
            np.savez_compressed(side, **{k.replace("/", "~"): v for k, v in self.sidecar.items()})
            meta["thermal:sidecar"] = side.name
        st.SetMetadata("customLayerData", meta)
        st.GetRootLayer().Save()
        return path

    def _jet_colors(self, mesh, surf, ts, rng):
        pts = np.array(mesh.GetPointsAttr().Get())
        col = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
        lo, hi = rng
        diffs = [e for e in self.scene.of(Element) if e.cls == "Diffuser" and e.attrs["ceiling_surface"] == surf.id]
        from ..render.shading import jet_delta
        for i in range(self.n):
            t = np.full(len(pts), ts[i], float)
            for d in diffs:
                td = self.scene.series[(d.id, "surfaceTemperature")][i]
                t += jet_delta(pts[:, 0], pts[:, 1], td, ts[i], self.scene.series[(d.id, "jetStrength")][i],
                               self.scene.series[(d.id, "jetLength")][i], d.attrs["jet_center"], d.attrs["jet_half"])
            c = CMAP(np.clip((t - lo) / max(hi - lo, 1e-6), 0, 1))[:, :3]
            col.Set(Vt.Vec3fArray([Gf.Vec3f(*map(float, x)) for x in c]), Usd.TimeCode(i))

    def _camera(self, cam: Camera):
        from ..render.cameras import look_at
        c = UsdGeom.Camera.Define(self.stage, f"/World/Cameras/{pname(cam.name)}")
        w, h = cam.resolution; ha = 20.955
        c.CreateHorizontalApertureAttr(ha); c.CreateVerticalApertureAttr(ha * h / w)
        c.CreateFocalLengthAttr(ha / (2 * np.tan(np.radians(cam.hfov_deg) / 2)))
        c.CreateClippingRangeAttr(Gf.Vec2f(0.03, 200))
        c.AddTransformOp().Set(look_at(cam.eye, cam.target))
        p = c.GetPrim()
        self.put(p, "thermal:resolution", T.Int2, Gf.Vec2i(int(w), int(h)))
        self.put(p, "thermal:band_um", T.Float2, Gf.Vec2f(*BAND_UM))
        self.put(p, "thermal:previewFrames", T.StringArray, cam.frames)
        for k in ("emissivity_setting", "reflected_setting_c", "netd_k", "blur_px"):
            self.put(p, f"thermal:camera:{k}", T.Float, float(self.cfg["camera"][k]))
        p.CreateRelationship("thermal:environment").SetTargets([Sdf.Path(self.paths[cam.space])])
        self.provenance(p, cam, {"pose": "ts:pose"})
