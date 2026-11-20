"""InspectionEnv: a Gymnasium-style environment in which an agent carries a thermal camera through a thermal scene.

    from thermoscene.embodied import ThermalWorld
    from thermoscene.embodied.env import InspectionEnv
    env = InspectionEnv(ThermalWorld("B_hvac_system.usda"), space="Plenum", task="fault_search")
    obs, info = env.reset(seed=0)
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())

The API follows Gymnasium (reset/step/render, action_space, observation_space). If the `gymnasium` package is
installed the class is a `gymnasium.Env` with Gymnasium spaces; otherwise minimal stand-in spaces with the same
`sample()`, `contains()` and `shape` are used, so the environment has no extra dependency.

Actions (discrete, default): 0 forward, 1 back, 2 strafe left, 3 strafe right, 4 turn left, 5 turn right,
6 tilt up, 7 tilt down, 8 wait (advance simulated time), 9 declare (the target is at the image centre).
Continuous mode: Box(7) = [forward, left, up (m), yaw, pitch (deg), wait (>0.5), declare (>0.5)].

Observations: dict(thermal = camera reading (deg C, h x w), pose = [x, y, z, yaw, pitch], time = [sin, cos] of the
hour of day and of the day of year). With privileged=True the labels (true temperature, category, instance, depth)
are added; they are always in `info`, with provenance and the fault mask.

Tasks:
* "fault_search": find the injected faults. Declaring while at least `detect_pixels` pixels of a fault not yet found
  lie in the central window scores +1; a declaration that finds nothing scores `false_alarm_penalty`. The episode
  ends when every fault is found.
* "coverage": inspect the generated elements. An element counts as inspected when it covers at least
  `detect_pixels` pixels at a ground sampling distance finer than `inspect_gsd_m`; +1 per new element.
Every step costs `step_penalty`. Each action advances the camera clock by `seconds_per_action` (drift, shutter);
"wait" advances simulated time by `wait_steps` EnergyPlus time steps.
"""
from __future__ import annotations

import numpy as np

from .sensor import ThermalCamera, agc8
from .world import Pose, ThermalWorld

try:                                   # optional dependency
    import gymnasium as _gym
    from gymnasium import spaces as _spaces
    _Base = _gym.Env
except ImportError:                    # minimal stand-ins with the same interface
    _gym = None

    class _Base:                       # noqa: D401
        metadata: dict = {}

    class _Discrete:
        def __init__(self, n, seed=None):
            self.n, self.shape, self._rng = int(n), (), np.random.default_rng(seed)

        def sample(self):
            return int(self._rng.integers(self.n))

        def contains(self, x):
            return isinstance(x, (int, np.integer)) and 0 <= int(x) < self.n

    class _Box:
        def __init__(self, low, high, shape=None, dtype=np.float32, seed=None):
            self.low = np.broadcast_to(np.asarray(low, dtype), shape or np.shape(low)).copy()
            self.high = np.broadcast_to(np.asarray(high, dtype), shape or np.shape(high)).copy()
            self.shape, self.dtype, self._rng = self.low.shape, dtype, np.random.default_rng(seed)

        def sample(self):
            lo = np.where(np.isfinite(self.low), self.low, -1.0); hi = np.where(np.isfinite(self.high), self.high, 1.0)
            return self._rng.uniform(lo, hi).astype(self.dtype)

        def contains(self, x):
            x = np.asarray(x)
            return x.shape == self.shape and bool(np.all(x >= self.low) and np.all(x <= self.high))

    class _Dict:
        def __init__(self, d):
            self.spaces = dict(d)

        def sample(self):
            return {k: v.sample() for k, v in self.spaces.items()}

        def contains(self, x):
            return all(k in x and v.contains(x[k]) for k, v in self.spaces.items())

        def __getitem__(self, k):
            return self.spaces[k]

    class _spaces:                     # noqa: N801
        Discrete, Box, Dict = _Discrete, _Box, _Dict


ACTIONS = ["forward", "back", "left", "right", "turn_left", "turn_right", "tilt_up", "tilt_down", "wait", "declare"]


class InspectionEnv(_Base):
    metadata = {"render_modes": ["rgb_array"], "render_fps": 4}

    def __init__(self, world: ThermalWorld | str, camera: str | ThermalCamera = "handheld_320", space: str = "Zone",
                 task: str = "fault_search", action_mode: str = "discrete", max_steps: int = 200,
                 randomize_camera: bool = True, privileged: bool = False, start_time: str | None = None,
                 move_m: float = 0.5, turn_deg: float = 30.0, tilt_deg: float = 15.0, wait_steps: int = 1,
                 seconds_per_action: float = 2.0, detect_pixels: int = 9, center_window: float = 0.3,
                 inspect_gsd_m: float = 0.03, step_penalty: float = 0.01, false_alarm_penalty: float = 0.1,
                 margin_m: float = 0.25, realism: dict | None = None, render_mode: str | None = "rgb_array"):
        self.world = world if isinstance(world, ThermalWorld) else ThermalWorld(world)
        self.base_camera = ThermalCamera.preset(camera) if isinstance(camera, str) else camera
        self.camera = self.base_camera
        self.space = self.world.space_path(space)
        self.task, self.mode, self.max_steps = task, action_mode, int(max_steps)
        self.randomize_camera, self.privileged, self.start_time = randomize_camera, privileged, start_time
        self.move_m, self.turn, self.tilt, self.wait_steps = move_m, turn_deg, tilt_deg, int(wait_steps)
        self.dt_action, self.detect_pixels, self.center_window = seconds_per_action, int(detect_pixels), center_window
        self.inspect_gsd, self.step_penalty, self.fa_penalty = inspect_gsd_m, step_penalty, false_alarm_penalty
        self.margin, self.realism, self.render_mode = margin_m, realism, render_mode
        w = self.world
        lo, hi = w.space_bounds.get(self.space, (np.full(3, -1e3), np.full(3, 1e3)))
        self.lo, self.hi = lo + margin_m, hi - margin_m
        if np.any(self.lo >= self.hi):                     # very thin space (e.g. a shallow plenum): keep the mid-plane
            mid = (lo + hi) / 2
            self.lo, self.hi = np.minimum(self.lo, mid), np.maximum(self.hi, mid)
        in_space = np.array([s == self.space for s in w.space_of])
        self.fault_ids = [k for k in range(w.np) if w.fault[k] and in_space[k]]
        self.element_ids = [k for k in range(w.np) if in_space[k] and w.sem[k] not in ("Surface", "Opening")]
        h, wd = self.camera.h, self.camera.w
        self.action_space = _spaces.Discrete(len(ACTIONS)) if action_mode == "discrete" else \
            _spaces.Box(np.array([-1, -1, -1, -45, -30, 0, 0], np.float32), np.array([1, 1, 1, 45, 30, 1, 1], np.float32))
        obs = {"thermal": _spaces.Box(-100.0, 300.0, shape=(h, wd), dtype=np.float32),
               "pose": _spaces.Box(-np.inf, np.inf, shape=(5,), dtype=np.float32),
               "time": _spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float32)}
        if privileged:
            obs |= {"true": _spaces.Box(-100.0, 300.0, shape=(h, wd), dtype=np.float32),
                    "category": _spaces.Box(-1, 255, shape=(h, wd), dtype=np.int32),
                    "instance": _spaces.Box(-1, 65535, shape=(h, wd), dtype=np.int32),
                    "depth": _spaces.Box(0.0, np.inf, shape=(h, wd), dtype=np.float32)}
        self.observation_space = _spaces.Dict(obs)
        self.rng = np.random.default_rng()
        self.frame = None

    # ------------------------------------------------------------------------------------------------ helpers
    def _valid(self, p):
        return bool(np.all(p >= self.lo) and np.all(p <= self.hi))

    def _blocked(self, a, b):
        """True if the straight move a -> b passes through geometry (with a margin)."""
        d = np.asarray(b, float) - a
        n = np.linalg.norm(d)
        if n < 1e-9:
            return False
        k, t = self.world.trace(np.asarray(a, float)[None], (d / n)[None], self.world.present[:, self.tc])
        return bool(k[0] >= 0 and t[0] < n + self.margin)

    def _time_features(self):
        lab = self.world.labels[self.tc].split()[-2:]
        mm, dd = (int(x) for x in lab[0].split("-")); hh, mi = (int(x) for x in lab[1].split(":"))
        hour = hh + mi / 60; doy = (np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30])[mm - 1] + dd)
        return np.array([np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
                         np.sin(2 * np.pi * doy / 365), np.cos(2 * np.pi * doy / 365)], np.float32)

    def _observe(self):
        pose = Pose.from_yaw_pitch(self.eye, self.yaw, self.pitch)
        self.frame = f = self.world.observe(pose, self.tc, self.camera, space=self.space, realism=self.realism,
                                            rng=self.rng, texture_seed=self.texture_seed)
        obs = dict(thermal=f.reading.astype(np.float32),
                   pose=np.array([*self.eye, self.yaw, self.pitch], np.float32), time=self._time_features())
        if self.privileged:
            obs |= dict(true=np.nan_to_num(f.true, nan=-273.15).astype(np.float32), category=f.category.astype(np.int32),
                        instance=f.instance.astype(np.int32), depth=f.depth.astype(np.float32))
        return obs

    def _info(self, **extra):
        f = self.frame
        return dict(time_label=f.time_label, time_index=f.time_index, true=f.true, category=f.category, instance=f.instance,
                    provenance=f.provenance, fault=f.fault, depth=f.depth, found=sorted(self.found),
                    n_faults=len(self.fault_ids), inspected=len(self.inspected), steps=self.steps, **extra)

    # ------------------------------------------------------------------------------------------------ API
    def reset(self, *, seed=None, options=None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
            if hasattr(self.action_space, "_rng"):
                self.action_space._rng = np.random.default_rng(seed)
        options = options or {}
        self.camera = self.base_camera.randomized(self.rng) if self.randomize_camera else self.base_camera
        self.camera.t_since_ffc = float(self.rng.uniform(0, self.camera.p.get("ffc_interval_s") or 0))
        self.texture_seed = int(self.rng.integers(1 << 20)) if self.randomize_camera else 0
        st = options.get("time", self.start_time)
        self.tc = self.world.time_index(st) if isinstance(st, str) else (int(st) if st is not None else int(self.rng.integers(self.world.n)))
        if "pose" in options:
            self.eye = np.asarray(options["pose"][:3], float); self.yaw, self.pitch = map(float, options["pose"][3:5])
        else:
            for _ in range(200):
                p = self.rng.uniform(self.lo, self.hi)
                if self.world.space_at(p) == self.space:
                    break
            self.eye, self.yaw, self.pitch = p, float(self.rng.uniform(-180, 180)), 0.0
        self.steps, self.found, self.inspected = 0, set(), set()
        return self._observe(), self._info()

    def step(self, action):
        a = np.asarray(action)
        fwd = np.array([np.cos(np.radians(self.yaw)), np.sin(np.radians(self.yaw)), 0.0])
        left = np.array([-fwd[1], fwd[0], 0.0])
        move, declare, wait, collided = np.zeros(3), False, False, False
        if self.mode == "discrete":
            name = ACTIONS[int(a)]
            move = {"forward": fwd, "back": -fwd, "left": left, "right": -left}.get(name, np.zeros(3)) * self.move_m
            self.yaw += {"turn_left": self.turn, "turn_right": -self.turn}.get(name, 0.0)
            self.pitch = float(np.clip(self.pitch + {"tilt_up": self.tilt, "tilt_down": -self.tilt}.get(name, 0.0), -85, 85))
            wait, declare = name == "wait", name == "declare"
        else:
            a = np.clip(a.astype(float), self.action_space.low, self.action_space.high)
            move = a[0] * fwd + a[1] * left + np.array([0, 0, a[2]])
            self.yaw += a[3]; self.pitch = float(np.clip(self.pitch + a[4], -85, 85))
            wait, declare = a[5] > 0.5, a[6] > 0.5
        self.yaw = (self.yaw + 180) % 360 - 180
        if np.any(move):
            new = self.eye + move
            if self._valid(new) and not self._blocked(self.eye, new):
                self.eye = new
            else:
                collided = True
        ffc = self.camera.advance(self.dt_action)
        if wait:
            self.tc = min(self.tc + self.wait_steps, self.world.n - 1)
            ffc |= self.camera.advance(self.wait_steps * self.world.step_minutes * 60)
        obs = self._observe()
        self.steps += 1
        reward, f = -self.step_penalty, self.frame
        hgt, wid = f.instance.shape
        cy, cx, ry, rx = hgt // 2, wid // 2, int(hgt * self.center_window / 2), int(wid * self.center_window / 2)
        centre = f.instance[cy - ry:cy + ry + 1, cx - rx:cx + rx + 1]
        new_found = []
        if self.task == "fault_search" and declare:
            for k in self.fault_ids:
                if k not in self.found and np.sum(centre == k) >= self.detect_pixels:
                    self.found.add(k); new_found.append(k)
            reward += len(new_found) if new_found else -self.fa_penalty
        elif self.task == "coverage":
            gsd = f.depth * 2 * self.camera.tx / self.camera.w
            for k in self.element_ids:
                if k not in self.inspected and np.sum((f.instance == k) & (gsd <= self.inspect_gsd)) >= self.detect_pixels:
                    self.inspected.add(k); reward += 1.0
        done = (self.task == "fault_search" and self.fault_ids and len(self.found) == len(self.fault_ids)) or \
               (self.task == "coverage" and self.element_ids and len(self.inspected) == len(self.element_ids))
        return obs, float(reward), bool(done), self.steps >= self.max_steps, \
            self._info(collided=collided, ffc=ffc, declared=declare, new_found=new_found)

    def render(self):
        if self.frame is None:
            return None
        from matplotlib import colormaps
        g = agc8(self.frame.reading)
        return (colormaps["inferno"](g / 255.0)[..., :3] * 255).astype(np.uint8)

    def close(self):
        pass
