"""Baseline agents in the inspection environment (fault search), to show that the environment is usable as is and how
hard label-free fault search is in realistic images.

Two agents search for the injected fault of case B's office (the wall void) from random start poses and times, with a
random unit of the 160 x 120 camera class (one sample per pixel for speed):

* random: uniform random actions;
* privileged (upper bound): the look-around search steered by the environment's fault mask instead of the detector;
* look-around: a label-free detector flags pixels whose reading departs from the median of their 15 x 15 neighbourhood by
  more than K times the image's robust noise, in blobs of at least MIN_BLOB pixels; the agent turns toward the largest
  blob and declares when it is centred, and turns away for three steps after a false declaration; with no blob it
  turns 30 degrees, and after a full turn it moves forward (turning when blocked).

Writes case_studies/env_baselines.json and docs/env_baselines.md.  Run:  python case_studies/env_baselines.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import label, median_filter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene.embodied import ThermalCamera, ThermalWorld  # noqa: E402
from thermoscene.embodied.env import ACTIONS, InspectionEnv  # noqa: E402

EPISODES, STEPS = 30, 60
K, MIN_BLOB = 4.0, 12
A = {a: i for i, a in enumerate(ACTIONS)}


def detect(img):
    """Largest anomalous blob: (row, col, size) or None."""
    res = img - median_filter(img, size=15)
    s = 1.4826 * np.median(np.abs(res - np.median(res)))
    m = np.abs(res) > K * max(s, 1e-6)
    lab, n = label(m)
    if n == 0:
        return None
    sizes = np.bincount(lab.ravel())[1:]
    k = int(np.argmax(sizes)) + 1
    if sizes[k - 1] < MIN_BLOB:
        return None
    ys, xs = np.nonzero(lab == k)
    return float(ys.mean()), float(xs.mean()), int(sizes[k - 1])


class LookAround:
    """Label-free: steer to the most anomalous blob and declare; after a false declaration, look elsewhere."""
    def __init__(self):
        self.turns, self.cooldown = 0, 0

    def target(self, obs, info):
        return detect(obs["thermal"])

    def __call__(self, obs, info):
        img = obs["thermal"]
        h, w = img.shape
        if info.get("declared") and not info.get("new_found"):
            self.cooldown = 3                        # that blob was not a fault: turn away for a few steps
        b = self.target(obs, info) if self.cooldown == 0 else None
        if self.cooldown:
            self.cooldown -= 1
        if b is not None:
            r, c, _ = b
            if abs(c - w / 2) < 0.12 * w and abs(r - h / 2) < 0.12 * h:
                return A["declare"]
            if abs(c - w / 2) >= 0.12 * w:
                return A["turn_left"] if c < w / 2 else A["turn_right"]
            return A["tilt_up"] if r < h / 2 else A["tilt_down"]
        self.turns += 1
        if self.turns % 12 == 0:
            return A["forward"]
        return A["turn_left"]


class Privileged(LookAround):
    """Upper bound: the same search, but the target is the fault mask from the environment's labels."""
    def target(self, obs, info):
        m = info.get("fault")
        if m is None or m.sum() < 9:
            return None
        ys, xs = np.nonzero(m)
        return float(ys.mean()), float(xs.mean()), int(m.sum())


def run(policy_name, env, rng):
    succ, steps, false_alarms = 0, [], 0
    for ep in range(EPISODES):
        obs, info = env.reset(seed=int(rng.integers(1 << 30)), options=dict(time=int(rng.integers(env.world.n))))
        pol = {"look-around": LookAround, "privileged": Privileged}.get(policy_name, lambda: None)()
        for t in range(STEPS):
            a = pol(obs, info) if pol else env.action_space.sample()
            obs, r, term, trunc, info = env.step(a)
            if info.get("declared") and not info.get("new_found"):
                false_alarms += 1
            if info.get("collided") and pol:
                pol.turns = 1                     # blocked: turn before trying again
            if term:
                succ += 1; steps.append(t + 1)
                break
            if trunc:
                break
    return dict(episodes=EPISODES, max_steps=STEPS, success=succ, success_rate=round(succ / EPISODES, 2),
                median_steps_to_success=float(np.median(steps)) if steps else None,
                false_alarms_per_episode=round(false_alarms / EPISODES, 2))


def main():
    w = ThermalWorld(ROOT / "case_studies" / "B_hvac_system" / "output" / "B_hvac_system.usda")
    cam = ThermalCamera.preset("lepton35", supersample=1)
    env = InspectionEnv(w, camera=cam, space="Zone", task="fault_search", max_steps=STEPS, randomize_camera=True)
    out = {}
    t0 = time.time()
    for name in ("random", "look-around", "privileged"):
        rng = np.random.default_rng(11)
        out[name] = run(name, env, rng)
        print(name, out[name])
    out["seconds"] = round(time.time() - t0)
    out["seconds_per_step"] = round(out["seconds"] / (3 * EPISODES * STEPS), 3)
    (ROOT / "case_studies" / "env_baselines.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    L = ["# Baseline agents in the inspection environment (fault search, case B office)", "",
         f"{EPISODES} episodes of at most {STEPS} steps per agent; random start pose, time step and camera unit (160 x 120 "
         f"class, one sample per pixel). Generated by `case_studies/env_baselines.py`.", "",
         "| Agent | Faults found (episodes) | Median steps to success | False declarations per episode |", "|---|---|---|---|"]
    for name in ("random", "look-around", "privileged"):
        r = out[name]
        L.append(f"| {name} | {r['success']} of {r['episodes']} | {r['median_steps_to_success']} | {r['false_alarms_per_episode']} |")
    L += ["", f"About {out['seconds_per_step']} s per environment step on one laptop core (wall-clock, other jobs running)."]
    (ROOT / "docs" / "env_baselines.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
