"""ThermalWorld: a built thermal scene loaded once for fast observation from any pose and any simulated time.

Geometry is rasterised on the CPU (z-buffer at pixel centres, perspective-correct depth, near-plane clipping), which
gives the same visibility as the reference ray caster's pixel-centre rays at a fraction of the cost, so an agent can
move freely. Secondary rays (specular reflections) are traced with a multi-origin version of the reference ray caster.

Radiometry is the reference radiometry (thermal.radiometry) plus optional scene realism (`defaults.yaml`, `realism`):
directional emissivity, one specular bounce on metals and glass, within-surface emissivity and temperature variation
(world-space value noise, so a surface looks the same from every pose) and LWIR path extinction. With realism off and
the "reference" camera, an observation reproduces `render.raycast` (tested).

Labels returned with every observation, per output pixel: the modelled true surface temperature, instance (scene
prim), semantic category, provenance level of the temperature at that time step (jets and stagnant fluid lower it, as
in render.shading.provenance_frame), effective emissivity, depth, fault mask and a mixed-pixel mask (sub-pixels of
more than one prim). The realism texture's temperature part is unmodelled variation by design: it is in the reading,
not in the label, as unmodelled variation is in a field measurement.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from pxr import Usd, UsdGeom

from ..render.raycast import fan, usd_faces
from ..render.shading import LEVELS, TEMP, Series, cameras, jet_delta, labels, thermal_prims
from ..thermal import radiometry as rad

_DEFAULTS = Path(__file__).resolve().parents[1] / "defaults.yaml"
SURFACE_CATEGORIES = ["Wall", "Floor", "Ceiling", "Roof", "Window", "Door", "Defect"]


def _cfg() -> dict:
    with open(_DEFAULTS, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------------------------------------- poses
@dataclass
class Pose:
    """Camera pose: eye position and rotation rows (right, up, back), the convention of render.cameras.look_at."""
    eye: np.ndarray
    R: np.ndarray

    @classmethod
    def look_at(cls, eye, target, up=(0.0, 0.0, 1.0)) -> "Pose":
        eye, target, up = (np.asarray(v, float) for v in (eye, target, up))
        f = target - eye; f = f / np.linalg.norm(f)
        if abs(f @ up) > 0.98:
            up = np.array([0.0, 1.0, 0.0])
        r = np.cross(f, up); r /= np.linalg.norm(r)
        u = np.cross(r, f)
        return cls(eye, np.stack([r, u, -f]))

    @classmethod
    def from_yaw_pitch(cls, eye, yaw_deg, pitch_deg) -> "Pose":
        y, p = np.radians(yaw_deg), np.radians(np.clip(pitch_deg, -85, 85))
        f = np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), np.sin(p)])
        return cls.look_at(eye, np.asarray(eye, float) + f)

    @property
    def forward(self):
        return -self.R[2]

    @property
    def yaw_pitch(self):
        f = self.forward
        return float(np.degrees(np.arctan2(f[1], f[0]))), float(np.degrees(np.arcsin(np.clip(f[2], -1, 1))))

    def key(self):
        return tuple(np.round(np.r_[self.eye, self.R.ravel()], 9))


@dataclass
class Frame:
    """One observation. Arrays are (h, w) at the camera's output resolution."""
    reading: np.ndarray            # camera reading, deg C
    true: np.ndarray               # modelled true surface temperature, deg C (centre sample)
    instance: np.ndarray           # index into world.prim_paths, -1 = background
    category: np.ndarray           # index into world.categories, -1 = background
    provenance: np.ndarray         # index into LEVELS, len(LEVELS) = background
    emissivity: np.ndarray         # effective (directional, textured) emissivity, 1 = background
    depth: np.ndarray              # distance along the optical axis, m (inf = background)
    fault: np.ndarray              # bool, pixel shows an injected fault
    mixed: np.ndarray              # bool, sub-pixels of more than one prim (edge pixel)
    pose: Pose
    time_index: int
    time_label: str
    space: str
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------- noise
def _hash01(ix, iy, iz, seed):
    """Deterministic uniform [-1, 1) per integer lattice point (splitmix64 of a spatial hash)."""
    x = (ix.astype(np.uint64) * np.uint64(73856093)) ^ (iy.astype(np.uint64) * np.uint64(19349663)) ^ \
        (iz.astype(np.uint64) * np.uint64(83492791)) ^ np.uint64(seed * 2654435761 % 2 ** 63)
    x = (x + np.uint64(0x9E3779B97F4A7C15))
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) / float(2 ** 53) * 2 - 1


def value_noise(p, seed, corr):
    """Smooth 3-D value noise with unit standard deviation and correlation length ~corr (two octaves)."""
    with np.errstate(over="ignore"):
        out = np.zeros(len(p))
        for octave, amp in ((1.0, 1.0), (2.0, 0.5)):
            q = np.asarray(p, float) / (corr / octave) + 1000.0
            i = np.floor(q).astype(np.int64); f = q - i
            s = f * f * (3 - 2 * f)
            acc = np.zeros(len(p))
            for dx in (0, 1):
                for dy in (0, 1):
                    for dz in (0, 1):
                        w = (s[:, 0] if dx else 1 - s[:, 0]) * (s[:, 1] if dy else 1 - s[:, 1]) * (s[:, 2] if dz else 1 - s[:, 2])
                        acc += w * _hash01(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz, seed + int(octave * 7919))
            out += amp * acc
    return out / 0.4136          # empirical std of the two-octave sum for uniform lattice values (unit-test checked)


# ---------------------------------------------------------------------------------------------------------- world
class ThermalWorld:
    def __init__(self, usd_path, realism: dict | None = None):
        cfg = _cfg()
        self.realism = copy.deepcopy(cfg["realism"])
        if realism is not None:
            self.realism = _merge(self.realism, realism)
        self.usd_path = str(usd_path)
        st = self.stage = Usd.Stage.Open(str(usd_path))
        self.series = Series(st)
        self.labels = labels(st)
        self.n = len(self.labels)
        meta = st.GetRootLayer().customLayerData
        self.step_minutes = float(meta.get("thermal:timestepMinutes", 15))
        self.prov_thr = float(meta.get("thermal:provenanceThresholdK", 0.1))
        prims = self.prims = thermal_prims(st)
        self.prim_paths = [str(p.GetPath()) for p in prims]
        self.np = len(prims)
        get = lambda p, a, d=None: (p.GetAttribute(a).Get() if p.HasAttribute(a) else d)
        self.T = np.stack([self._full(p, TEMP) for p in prims])                       # (prims, times)
        self.eps = np.array([float(get(p, "thermal:emissivity", 0.9)) for p in prims])
        self.sem = [get(p, "semantic:class", "") for p in prims]
        self.stype = [get(p, "ts:surfaceType", "") for p in prims]
        self.material = [get(p, "mep:material") or ("glazing" if get(p, "ts:surfaceType") == "Window" else "surface")
                         for p in prims]
        self.space_of = []
        for p in prims:
            rel = p.GetRelationship("ts:locatedIn")
            self.space_of.append(str(rel.GetTargets()[0]) if rel and rel.GetTargets() else "")
        self.fault = np.array([bool(get(p, "ts:fault")) or get(p, "ts:surfaceType") == "Defect" for p in prims])
        cats = SURFACE_CATEGORIES + sorted({s for s, t in zip(self.sem, self.stype) if s not in ("Surface", "Opening")})
        self.categories = cats
        self.cat = np.array([cats.index(t) if s in ("Surface", "Opening") and t in cats else
                             (cats.index(s) if s in cats else -1) for s, t in zip(self.sem, self.stype)])
        lv = []
        for p in prims:
            a = get(p, TEMP + ":provenance")
            lv.append(LEVELS.index(a) if a in LEVELS else len(LEVELS))
        self.base_level = np.array(lv)
        self.stagnant = np.zeros((self.np, self.n), bool)
        for k, p in enumerate(prims):
            if p.HasAttribute("mep:stagnation") and p.HasAttribute("mep:fluidMassFlow"):
                self.stagnant[k] = self._full(p, "mep:fluidMassFlow") <= 0
        self.present = np.ones((self.np, self.n), bool)
        for k, p in enumerate(prims):
            if p.HasAttribute("thermal:present"):
                self.present[k] = self._full(p, "thermal:present").astype(bool)
        spec = self.realism["specularity"]
        self.spec = np.array([float(spec.get(m, spec.get("surface", 0.0))) for m in self.material])
        tex = self.realism["texture_emissivity_scale"]
        self.tex_eps_scale = np.array([float(tex.get(m, 1.0)) for m in self.material])
        # jets
        index = {pp: i for i, pp in enumerate(self.prim_paths)}
        self.jets = []
        for k, p in enumerate(prims):
            rel = p.GetRelationship("thermal:ceilingJetTarget")
            if rel and rel.GetTargets() and str(rel.GetTargets()[0]) in index:
                c = p.GetAttribute("thermal:ceilingJetCenter").Get()
                self.jets.append(dict(k=k, j=index[str(rel.GetTargets()[0])], center=(c[0], c[1]),
                                      half=float(p.GetAttribute("thermal:ceilingJetHalfSize").Get()),
                                      strength=self._full(p, "thermal:ceilingJetStrength"),
                                      length=self._full(p, "thermal:ceilingJetDecayLength")))
        # spaces and their environments
        self.spaces = {}
        for sp in st.GetPrimAtPath("/World").GetChildren():
            c = sp.GetAttribute("semantic:class").Get() if sp.HasAttribute("semantic:class") else None
            if c in ("Zone", "Plenum", "Outdoors"):
                env = {q: self._full(sp, f"thermal:{q}") for q in ("reflectedTemperature", "backgroundTemperature", "airTemperature")
                       if sp.HasAttribute(f"thermal:{q}")}
                self.spaces[str(sp.GetPath())] = dict(cls=c, **env)
        # triangles
        tris, owner = [], []
        for k, p in enumerate(prims):
            t = fan(usd_faces(p))
            if len(t):
                tris.append(t); owner += [k] * len(t)
        self.V = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
        self.tri_prim = np.array(owner, int)
        nrm = np.cross(self.V[:, 1] - self.V[:, 0], self.V[:, 2] - self.V[:, 0])
        self.tri_n = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        self.prim_lo = np.array([self.V[self.tri_prim == k].reshape(-1, 3).min(0) if np.any(self.tri_prim == k) else np.full(3, np.inf)
                                 for k in range(self.np)]) - 1e-4
        self.prim_hi = np.array([self.V[self.tri_prim == k].reshape(-1, 3).max(0) if np.any(self.tri_prim == k) else np.full(3, -np.inf)
                                 for k in range(self.np)]) + 1e-4
        self.space_bounds = {}
        for sp in self.spaces:
            ks = [k for k in range(self.np) if self.space_of[k] == sp and self.sem[k] in ("Surface", "Opening")]
            if ks:
                self.space_bounds[sp] = (self.prim_lo[ks].min(0), self.prim_hi[ks].max(0))
        self._raster_cache: dict = {}

    # ------------------------------------------------------------------------------------------------ loading
    def _full(self, prim, name) -> np.ndarray:
        a = prim.GetAttribute(name)
        if self.series.npz is not None:
            key = a.GetCustomDataByKey("thermal:sidecarKey")
            if key:
                return np.asarray(self.series.npz[key.replace("/", "~")], float)
        ts = a.GetTimeSamples()
        if not ts:
            return np.full(self.n, float(a.Get()))
        return np.array([float(a.Get(Usd.TimeCode(i))) for i in range(self.n)])

    def time_index(self, label: str) -> int:
        from ..render.cameras import resolve_frames
        return self.labels.index(resolve_frames([label], self.labels)[0])

    def camera_poses(self) -> dict:
        """The scene's own cameras: name -> (Pose, tan half-extents, resolution, space path)."""
        out = {}
        for cam in cameras(self.stage):
            c = UsdGeom.Camera(cam)
            M = np.array(UsdGeom.Xformable(cam).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
            fl = c.GetFocalLengthAttr().Get()
            tx, ty = c.GetHorizontalApertureAttr().Get() / 2 / fl, c.GetVerticalApertureAttr().Get() / 2 / fl
            env = cam.GetRelationship("thermal:environment").GetTargets()
            out[cam.GetName()] = (Pose(M[3, :3].copy(), M[:3, :3].copy()), (tx, ty),
                                  tuple(cam.GetAttribute("thermal:resolution").Get()), str(env[0]) if env else None)
        return out

    def space_path(self, space) -> str:
        """A space given as a path, a class ('Zone', 'Plenum', 'Outdoors') or None (first zone)."""
        if space in self.spaces:
            return space
        want = (space or "Zone").capitalize()
        for p, s in self.spaces.items():
            if s["cls"] == want:
                return p
        raise KeyError(f"no space {space}; spaces: {list(self.spaces)}")

    def space_at(self, point) -> str:
        """The space containing a point (zone and plenum bounding boxes, else the outdoors)."""
        pt = np.asarray(point, float)
        for want in ("Zone", "Plenum"):
            for p, (lo, hi) in self.space_bounds.items():
                if self.spaces[p]["cls"] == want and np.all(pt >= lo + 1e-6) and np.all(pt <= hi - 1e-6):
                    return p
        return self.space_path("Outdoors") if any(s["cls"] == "Outdoors" for s in self.spaces.values()) else next(iter(self.spaces))

    def prim_index(self, name_or_path: str) -> int:
        """Index of a prim given its path, its name or the end of its path."""
        for k, p in enumerate(self.prim_paths):
            if p == name_or_path or p.endswith("/" + name_or_path):
                return k
        raise KeyError(name_or_path)

    def view_of(self, k: int, distance: float, space=None, height: float | None = None, n_dirs: int = 72) -> Pose | None:
        """A pose `distance` metres from the centre of prim k, facing it as squarely as possible with an unobstructed
        line of sight, with the eye inside `space` (default: the prim's own space; for an outside face, the outdoors).
        `height` fixes the eye's z (e.g. a robot's camera mast); by default the eye is level with the target."""
        c = (self.prim_lo[k] + self.prim_hi[k]) / 2
        tris = self.V[self.tri_prim == k]
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]).sum(0)
        n = n / (np.linalg.norm(n) or 1.0)
        space = self.space_path(space) if space else self.space_of[k]
        best, best_score = None, -1.0
        for a in np.linspace(0, 2 * np.pi, n_dirs, endpoint=False):
            for zoff in ([0.0] if height is None else [height - c[2]]):
                d = np.array([np.cos(a), np.sin(a), 0.0])
                eye = c + distance * d + [0.0, 0.0, zoff]
                if self.space_at(eye) != space:
                    continue
                dirn = (c - eye) / np.linalg.norm(c - eye)
                hit, _ = self.trace(eye[None], dirn[None])
                if hit[0] != k:
                    continue
                score = abs(float(dirn @ n)) if np.linalg.norm(n) > 0 else 1.0
                if score > best_score:
                    best, best_score = Pose.look_at(eye, c), score
        return best

    # ------------------------------------------------------------------------------------------------ visibility
    def rasterize(self, pose: Pose, tx: float, ty: float, W: int, H: int, active=None, near: float = 1e-3):
        """(owner prim, depth along -z, triangle) per pixel centre of a W x H image; -1 / inf where nothing is hit."""
        key = (pose.key(), round(tx, 9), round(ty, 9), W, H, None if active is None else active.tobytes())
        if key in self._raster_cache:
            return self._raster_cache[key]
        sel = np.ones(len(self.V), bool) if active is None else active[self.tri_prim]
        ids = np.nonzero(sel)[0]
        P = (self.V[sel] - pose.eye) @ pose.R.T                     # camera coordinates (x right, y up, z back)
        d = -P[..., 2]
        front = d > near
        full = front.all(1)
        part = front.any(1) & ~full
        tris, tid = [P[full]], [ids[full]]
        for k in np.nonzero(part)[0]:                               # clip against the near plane (Sutherland-Hodgman)
            poly, pts = [], P[k]
            for a in range(3):
                pa, pb = pts[a], pts[(a + 1) % 3]
                ina, inb = -pa[2] > near, -pb[2] > near
                if ina:
                    poly.append(pa)
                if ina != inb:
                    s = (near + pa[2]) / (pa[2] - pb[2])
                    poly.append(pa + s * (pb - pa))
            for j in range(1, len(poly) - 1):
                tris.append(np.array([[poly[0], poly[j], poly[j + 1]]])); tid.append(np.array([ids[k]]))
        tris = np.concatenate(tris); tid = np.concatenate(tid)
        zbuf = np.full((H, W), np.inf); tri = np.full((H, W), -1)
        if len(tris):
            dd = -tris[..., 2]
            u = (tris[..., 0] / dd / tx + 1) / 2 * W
            v = (1 - tris[..., 1] / dd / ty) / 2 * H
            j0 = np.maximum(np.ceil(u.min(1) - 0.5), 0).astype(int); j1 = np.minimum(np.floor(u.max(1) - 0.5), W - 1).astype(int)
            i0 = np.maximum(np.ceil(v.min(1) - 0.5), 0).astype(int); i1 = np.minimum(np.floor(v.max(1) - 0.5), H - 1).astype(int)
            area = (u[:, 1] - u[:, 0]) * (v[:, 2] - v[:, 0]) - (u[:, 2] - u[:, 0]) * (v[:, 1] - v[:, 0])
            ok = (j0 <= j1) & (i0 <= i1) & (np.abs(area) > 1e-14)
            for t in np.nonzero(ok)[0]:
                pu = np.arange(j0[t], j1[t] + 1) + 0.5
                pv = (np.arange(i0[t], i1[t] + 1) + 0.5)[:, None]
                (u0, u1, u2), (v0, v1, v2), A = u[t], v[t], area[t]
                w0 = ((u1 - pu) * (v2 - pv) - (u2 - pu) * (v1 - pv)) / A
                w1 = ((u2 - pu) * (v0 - pv) - (u0 - pu) * (v2 - pv)) / A
                w2 = 1.0 - w0 - w1
                inside = (w0 >= -1e-12) & (w1 >= -1e-12) & (w2 >= -1e-12)
                if not inside.any():
                    continue
                depth = 1.0 / (w0 / dd[t, 0] + w1 / dd[t, 1] + w2 / dd[t, 2])
                zb = zbuf[i0[t]:i1[t] + 1, j0[t]:j1[t] + 1]
                closer = inside & (depth < zb)
                if closer.any():
                    zb[closer] = depth[closer]
                    tri[i0[t]:i1[t] + 1, j0[t]:j1[t] + 1][closer] = tid[t]
        owner = np.where(tri >= 0, self.tri_prim[np.maximum(tri, 0)], -1)
        out = (owner, zbuf, tri)
        if len(self._raster_cache) > 64:
            self._raster_cache.clear()
        self._raster_cache[key] = out
        return out

    def ray_dirs(self, pose: Pose, tx, ty, W, H):
        jj, ii = np.meshgrid(np.arange(W), np.arange(H))
        x = ((jj + 0.5) / W * 2 - 1) * tx
        y = (1 - (ii + 0.5) / H * 2) * ty
        return np.stack([x, y, -np.ones_like(x)], -1) @ pose.R                   # camera z = -1, as camera_rays

    def trace(self, origins, dirs, active=None, chunk=2_000_000):
        """Nearest prim hit by rays with individual origins (multi-origin version of render.raycast.raycast):
        per-prim bounding-box culling, then a Moller-Trumbore test vectorised over rays x the prim's triangles."""
        n = len(dirs)
        best_t = np.full(n, np.inf); best = np.full(n, -1)
        inv = 1.0 / np.where(np.abs(dirs) < 1e-12, 1e-12, dirs)
        if not hasattr(self, "_prim_tris"):
            self._prim_tris = [self.V[self.tri_prim == k] for k in range(self.np)]
        for k in range(self.np):
            if active is not None and not active[k]:
                continue
            t0, t1 = (self.prim_lo[k] - origins) * inv, (self.prim_hi[k] - origins) * inv
            tmin = np.minimum(t0, t1).max(1); tmax = np.maximum(t0, t1).min(1)
            sel = np.nonzero((tmax >= np.maximum(tmin, 0)) & (tmin < best_t))[0]
            tris = self._prim_tris[k]
            if not len(sel) or not len(tris):
                continue
            a, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]          # (t, 3)
            step = max(1, chunk // len(tris))
            for s0 in range(0, len(sel), step):
                ss = sel[s0:s0 + step]
                o, d = origins[ss][:, None, :], dirs[ss][:, None, :]                          # (m, 1, 3)
                p = np.cross(d, e2[None]); det = np.einsum("mtk,tk->mt", p, e1)
                okd = np.abs(det) > 1e-12
                invd = np.where(okd, 1 / np.where(okd, det, 1), 0)
                s = o - a[None]
                uu = np.einsum("mtk,mtk->mt", p, s) * invd
                q = np.cross(s, e1[None])
                vv = np.einsum("mtk,mtk->mt", np.broadcast_to(d, q.shape), q) * invd
                t = np.einsum("mtk,tk->mt", q, e2) * invd
                hit = okd & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (t > 1e-6)
                t = np.where(hit, t, np.inf).min(1)
                better = t < best_t[ss]
                best_t[ss[better]] = t[better]; best[ss[better]] = k
        return best, best_t

    # ------------------------------------------------------------------------------------------------ shading
    def scene_radiance(self, pose, tc, space, tx, ty, W, H, realism=None, texture_seed=0, stride=1):
        """Scene radiance per pixel centre of a W x H image plus the per-pixel quantities used for labels."""
        rz = self.realism if realism is None else _merge(self.realism, realism)
        active = self.present[:, tc] if not self.present[:, tc].all() else None
        owner, depth, tri = self.rasterize(pose, tx, ty, W, H, active)
        sp = self.spaces[space]
        t_r, t_bg = sp["reflectedTemperature"][tc], sp["backgroundTemperature"][tc]
        hit = owner >= 0
        o = np.maximum(owner, 0)
        true = np.where(hit, self.T[o, tc], np.nan)
        dirs = self.ray_dirs(pose, tx, ty, W, H)
        pts = pose.eye + dirs * np.where(np.isfinite(depth), depth, 0)[..., None]
        jet_change = np.zeros((H, W))
        for jt in self.jets:
            m = owner == jt["j"]
            if m.any():
                dlt = jet_delta(pts[m][:, 0], pts[m][:, 1], self.T[jt["k"], tc], self.T[jt["j"], tc], jt["strength"][tc],
                                jt["length"][tc], jt["center"], jt["half"])
                true[m] += dlt; jet_change[m] += dlt
        eps = np.where(hit, self.eps[o], 1.0)
        t_shade = true.copy()
        tex = rz["texture"]
        # smooth fields (texture, specular reflection) are evaluated on every `stride`-th sub-pixel and spread over
        # the sub-pixels of the same prim in that block
        st = max(1, int(stride))
        c0 = st // 2
        sub = (slice(c0, None, st), slice(c0, None, st))
        spread = lambda a: np.repeat(np.repeat(a, st, 0), st, 1)[:H, :W]
        same = (spread(owner[sub]) == owner) & hit
        if tex.get("enabled") and hit.any():
            oc, hc = o[sub], hit[sub]
            P = pts[sub][hc]
            nT = np.zeros(oc.shape); nE = np.zeros(oc.shape)
            vT, vE = np.zeros(len(P)), np.zeros(len(P))
            for k in np.unique(oc[hc]):
                m = oc[hc] == k
                vT[m] = value_noise(P[m], int(k) * 1009 + texture_seed, tex["correlation_m"])
                vE[m] = value_noise(P[m], int(k) * 1009 + texture_seed + 7, tex["correlation_m"])
            nT[hc], nE[hc] = vT, vE
            nT, nE = np.where(same, spread(nT), 0.0), np.where(same, spread(nE), 0.0)
            t_shade = np.where(hit, true + tex["temperature_k"] * nT, true)
            eps = np.where(hit, np.clip(eps + tex["emissivity"] * self.tex_eps_scale[o] * nE, 0.02, 0.995), eps)
        cos_t = np.ones((H, W))
        if rz.get("angular_emissivity") or rz.get("specular_reflections"):
            n = self.tri_n[np.maximum(tri, 0)]
            dn = dirs / np.linalg.norm(dirs, axis=-1, keepdims=True)
            cos_t = np.where(hit, np.abs(np.einsum("hwk,hwk->hw", n, dn)), 1.0)
        if rz.get("angular_emissivity"):
            eps = np.where(hit, eps * (1 - (1 - np.clip(cos_t, 0, 1)) ** 5), eps)
        L_refl = np.full((H, W), float(rad.radiance(t_r)))
        if rz.get("specular_reflections"):
            s = np.where(hit, self.spec[o], 0.0)
            # trace only where the specular term can matter: (1 - eps) * specularity above min_specular_weight
            m_all = hit & ((1 - eps) * s >= float(rz.get("min_specular_weight", 0.02)))
            m = np.zeros_like(hit); m[sub] = m_all[sub]
            if m.any():
                n = self.tri_n[tri[m]]
                d = dirs[m] / np.linalg.norm(dirs[m], axis=1, keepdims=True)
                n = np.where((np.einsum("ij,ij->i", n, d) > 0)[:, None], -n, n)   # normal facing the camera
                r = d - 2 * np.einsum("ij,ij->i", d, n)[:, None] * n
                k2, _ = self.trace(pts[m] + n * 1e-4, r, active)
                T2 = np.where(k2 >= 0, self.T[np.maximum(k2, 0), tc], t_bg)
                e2 = np.where(k2 >= 0, self.eps[np.maximum(k2, 0)], 1.0)
                L2 = e2 * rad.radiance(T2) + (1 - e2) * rad.radiance(t_r)
                Ls = np.full(m.shape, np.nan); Ls[m] = L2
                Ls = np.where(same & m_all, spread(Ls[sub]), np.nan)
                ok = np.isfinite(Ls)
                L_refl[ok] = s[ok] * Ls[ok] + (1 - s[ok]) * L_refl[ok]
        L = np.where(hit, eps * rad.radiance(t_shade) + (1 - eps) * L_refl, rad.radiance(t_bg))
        beta = float(rz.get("atmosphere_extinction_per_m") or 0.0)
        if beta > 0 and hit.any() and "airTemperature" in sp:
            dist = np.where(hit, depth * np.linalg.norm(dirs, axis=-1), 0.0)
            tau = np.exp(-beta * dist)
            L = tau * L + (1 - tau) * rad.radiance(sp["airTemperature"][tc])
        # provenance at this time step
        prov = np.where(hit, self.base_level[o], len(LEVELS))
        heur = LEVELS.index("heuristic")
        stag = hit & self.stagnant[o, tc] & (prov < heur)
        prov[stag] = heur
        jm = hit & (np.abs(jet_change) >= self.prov_thr) & (prov < heur)
        prov[jm] = heur
        return dict(L=L, owner=owner, depth=depth, true=true, eps=eps, prov=prov)

    def contrast_series(self, pose: Pose, k: int, host: int, tx: float, ty: float, W: int, H: int, space=None,
                        realism=None, texture_seed=0, cam_settings=(0.95, 20.0), max_pixels=3000, chunk=2048, seed=0):
        """Apparent contrast between prim k and a ring of `host` pixels around it, for every time step of the scene, as
        an ideal sensor with the given emissivity and reflected-temperature settings reads it from `pose`.

        Geometry, directional emissivity, specular paths and within-surface variation are fixed for the pose and
        evaluated once; only temperatures change with time, so a year of 15-minute steps costs seconds. Generated
        occupants are left out (they come and go). Contrast = median over the fault - median over the ring, the ring
        being host pixels 3 to max(6, sqrt(N)) pixels from the fault, as in case_studies/measurement_gap.py."""
        from scipy.ndimage import distance_transform_edt
        rz = self.realism if realism is None else _merge(self.realism, realism)
        dyn = ~self.present.all(axis=1)
        active = ~dyn if dyn.any() else None
        owner, depth, tri = self.rasterize(pose, tx, ty, W, H, active)
        space = self.space_path(space) if space else self.space_at(pose.eye)
        sp = self.spaces[space]
        F = owner == k
        n = int(F.sum())
        if n == 0:
            raise ValueError("the target is not visible from this pose")
        d = distance_transform_edt(~F)
        R = (owner == host) & (d > 2) & (d <= max(6.0, np.sqrt(n)))
        rng = np.random.default_rng(seed)
        pick = lambda m: (lambda idx: idx[rng.permutation(len(idx))[:max_pixels]])(np.flatnonzero(m.ravel()))
        iF, iR = pick(F), pick(R)
        idx = np.r_[iF, iR]
        o = owner.ravel()[idx]
        dirs = self.ray_dirs(pose, tx, ty, W, H).reshape(-1, 3)[idx]
        pts = pose.eye + dirs * depth.ravel()[idx][:, None]
        eps = self.eps[o].astype(float)
        nT = np.zeros(len(idx))
        tex = rz["texture"]
        if tex.get("enabled"):
            for kk in np.unique(o):
                m = o == kk
                nT[m] = value_noise(pts[m], int(kk) * 1009 + texture_seed, tex["correlation_m"])
                eps[m] = np.clip(eps[m] + tex["emissivity"] * self.tex_eps_scale[kk] *
                                 value_noise(pts[m], int(kk) * 1009 + texture_seed + 7, tex["correlation_m"]), 0.02, 0.995)
        nrm = self.tri_n[tri.ravel()[idx]]
        dn = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        if rz.get("angular_emissivity"):
            eps = eps * (1 - (1 - np.clip(np.abs(np.einsum("ij,ij->i", nrm, dn)), 0, 1)) ** 5)
        s = np.zeros(len(idx)); k2 = np.full(len(idx), -1)
        if rz.get("specular_reflections"):
            s = self.spec[o].astype(float)
            m = (1 - eps) * s >= float(rz.get("min_specular_weight", 0.02))
            if m.any():
                nn = np.where((np.einsum("ij,ij->i", nrm[m], dn[m]) > 0)[:, None], -nrm[m], nrm[m])
                r = dn[m] - 2 * np.einsum("ij,ij->i", dn[m], nn)[:, None] * nn
                k2[m], _ = self.trace(pts[m] + nn * 1e-4, r, active)
            s = np.where(m, s, 0.0)
        e2 = np.where(k2 >= 0, self.eps[np.maximum(k2, 0)], 1.0)
        nf = len(iF)
        out = np.empty(self.n)
        for t0 in range(0, self.n, chunk):
            t1 = min(self.n, t0 + chunk)
            tr = sp["reflectedTemperature"][t0:t1][None, :]
            tbg = sp["backgroundTemperature"][t0:t1][None, :]
            Lr = rad.radiance(tr)
            T = self.T[o, t0:t1] + tex.get("temperature_k", 0.0) * nT[:, None] * bool(tex.get("enabled"))
            T2 = np.where((k2 >= 0)[:, None], self.T[np.maximum(k2, 0), t0:t1], tbg)
            L2 = e2[:, None] * rad.radiance(T2) + (1 - e2[:, None]) * Lr
            L = eps[:, None] * rad.radiance(T) + (1 - eps[:, None]) * (s[:, None] * L2 + (1 - s[:, None]) * Lr)
            app = rad.apparent_temperature(L, *cam_settings)
            out[t0:t1] = np.median(app[:nf], axis=0) - np.median(app[nf:], axis=0)
        return out, dict(fault_pixels=n, ring_pixels=int(R.sum()), sampled=(len(iF), len(iR)))

    def observe(self, pose: Pose, tc: int, camera, space=None, realism=None, frame_seed=0, rng=None,
                texture_seed=0) -> Frame:
        """What `camera` reports from `pose` at time step `tc`, with per-pixel labels."""
        space = self.space_path(space) if space else self.space_at(pose.eye)
        W, H, txr, tyr = camera.render_grid()
        rz = realism
        if camera.reference and realism is None:
            rz = dict(angular_emissivity=False, specular_reflections=False, texture=dict(enabled=False),
                      atmosphere_extinction_per_m=0.0)
        f = self.scene_radiance(pose, tc, space, txr, tyr, W, H, rz, texture_seed, camera.ss)
        smap = camera.sample_map()
        if smap is not None:
            col, row = smap
            f = {k: (_bilinear(v, col, row) if k == "L" else _nearest(v, col, row)) for k, v in f.items()}
        L = camera.integrate(f["L"], "mean")
        reading = camera.measure(L, rng=rng, frame_seed=frame_seed)
        c = lambda a: camera.integrate(a, "center")
        owner = c(f["owner"])
        if camera.ss > 1:
            blk = f["owner"].reshape(camera.h, camera.ss, camera.w, camera.ss)
            mixed = (blk != blk[:, :1, :, :1]).any(axis=(1, 3))
        else:
            mixed = np.zeros(owner.shape, bool)
        cat = np.where(owner >= 0, self.cat[np.maximum(owner, 0)], -1)
        return Frame(reading=reading, true=c(f["true"]), instance=owner, category=cat, provenance=c(f["prov"]),
                     emissivity=c(f["eps"]), depth=c(f["depth"]), fault=(owner >= 0) & self.fault[np.maximum(owner, 0)],
                     mixed=mixed, pose=pose, time_index=int(tc), time_label=self.labels[tc], space=space,
                     meta=dict(camera=camera.name, reference=camera.reference))


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _bilinear(img, col, row):
    H, W = img.shape
    c = np.clip(col, 0, W - 1); r = np.clip(row, 0, H - 1)
    c0 = np.floor(c).astype(int); r0 = np.floor(r).astype(int)
    c1 = np.minimum(c0 + 1, W - 1); r1 = np.minimum(r0 + 1, H - 1)
    fc, fr = c - c0, r - r0
    return (img[r0, c0] * (1 - fc) * (1 - fr) + img[r0, c1] * fc * (1 - fr) + img[r1, c0] * (1 - fc) * fr + img[r1, c1] * fc * fr)


def _nearest(img, col, row):
    H, W = img.shape
    return img[np.clip(np.round(row).astype(int), 0, H - 1), np.clip(np.round(col).astype(int), 0, W - 1)]
