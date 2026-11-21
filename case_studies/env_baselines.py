"""Experiment E4: agents in the inspection environment (fault search), with and without the defect detector learned in
thermoscene (experiment E3), to show that perception learned in the environments can steer an inspection agent in
buildings it was not trained on, and how hard label-free fault search is in realistic images.

Agents search for the injected wall void of case B's office (Phoenix) and of case C's top-floor perimeter office
(Chicago) from random start poses and times, with a random unit of the 160 x 120 camera class (one sample per pixel for
speed). Neither building was used to train the detector (case C's geometry is excluded from its training data):

* random: uniform random actions (discrete action mode);
* look-around: a label-free detector flags pixels whose reading departs from the median of their 15 x 15 neighbourhood by
  more than K times the image's robust noise, in blobs of at least MIN_BLOB pixels, and steers the search below;
* hot-spot: the same search, steered by the better label-free detector of experiment E3, a thermographer's hot- and
  cold-spot search (pixels departing from the image median by at least the threshold tuned in E3, in kelvin; blobs of
  at least 4 pixels);
* learned: the same search, steered by the defect detector trained only on thermoscene scenes of other buildings
  (case_studies/detector_train.py, weights in case_studies/detector_model.pt, threshold tuned on its validation
  buildings; blobs of at least 4 pixels; needs PyTorch);
* learned-true, learned-ideal: the same search and network, trained in E3 on the true temperature fields or on
  idealised images of the same views (first seed, models in case_studies/detector/ from detector_train.py, their own
  validation thresholds), fed the robot's camera readings;
* privileged (upper bound): the same search steered by the environment's fault mask.
The search (class Searcher, continuous action mode) is the same for every detector: declare a candidate blob that is
centred; otherwise turn and tilt by the angle that centres the largest candidate, declaring after at most MAX_AIM
corrections; otherwise turn 30 degrees with the camera levelled, and move 0.5 m forward after a full turn (turning when
blocked). A candidate in a direction already declared without finding a fault (within REJECT_DEG, seen from within
REJECT_M) is skipped. Every agent meets the same episodes (start pose, time step and camera unit from the same seeds).

An earlier controller turned in fixed 30-degree steps toward the largest blob and looked away for three steps after a
false declaration; it swung between two headings when a target lay between them and was blind when the fault came
into view after a false declaration, which held every agent, the privileged one included, to few successes.

Writes case_studies/env_baselines.json and docs/env_baselines.md; agents run with --agents are merged into the
existing results; --perception adds the perception check (perception_check) without re-running the agents.
Run:  python case_studies/env_baselines.py [--agents name ...] [--cases B C] [--perception]
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
from thermoscene.embodied.env import InspectionEnv  # noqa: E402

EPISODES, STEPS = 30, 60
K, MIN_BLOB = 4.0, 12
CENTRE = 0.12                    # a candidate is centred when its centroid is within 12 % of the image size of the centre
MAX_AIM = 4                      # corrections toward one candidate before declaring it anyway
REJECT_DEG, REJECT_M = 12.0, 1.0
SEARCH_TURN_DEG, FORWARD_M, TURNS_PER_LAP = 30.0, 0.5, 12


def blobs(mask, min_px):
    """Connected blobs of at least min_px pixels, largest first: [(row, col, size), ...]."""
    lab, n = label(mask)
    if n == 0:
        return []
    sizes = np.bincount(lab.ravel())[1:]
    out = []
    for k in np.argsort(-sizes, kind="stable"):
        if sizes[k] < min_px:
            break
        ys, xs = np.nonzero(lab == k + 1)
        out.append((float(ys.mean()), float(xs.mean()), int(sizes[k])))
    return out


def anomaly_mask(img):
    """Pixels whose reading departs from their 15 x 15 median by more than K times the image's robust noise."""
    res = img - median_filter(img, size=15)
    s = 1.4826 * np.median(np.abs(res - np.median(res)))
    return np.abs(res) > K * max(s, 1e-6)


def _direction(yaw_deg, pitch_deg):
    y, p = np.radians(yaw_deg), np.radians(pitch_deg)
    return np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), np.sin(p)])


class Searcher:
    """The search shared by every detector-steered agent (continuous actions [forward, left, up, yaw, pitch, wait,
    declare]); subclasses supply the candidate blobs, largest first."""
    min_px = 4

    def __init__(self, camera):
        self.tx, self.ty = camera.tx, camera.ty            # tan of the half fields of view
        self.turns, self.aim, self.rejected, self.pending = 0, 0, [], None

    def candidates(self, obs, info):
        raise NotImplementedError

    def offset(self, r, c, h, w):
        """Yaw (left positive) and pitch (up positive) in degrees that bring pixel (r, c) to the image centre."""
        x = ((c + 0.5) / w * 2 - 1) * self.tx
        y = (1 - (r + 0.5) / h * 2) * self.ty
        return -float(np.degrees(np.arctan(x))), float(np.degrees(np.arctan(y)))

    def __call__(self, obs, info):
        img, pose = obs["thermal"], obs["pose"].astype(float)
        h, w = img.shape
        eye = pose[:3]
        if info.get("declared") and not info.get("new_found") and self.pending is not None:
            self.rejected.append(self.pending)               # that direction holds no fault: skip it from now on
        self.pending = None
        cos_rej = np.cos(np.radians(REJECT_DEG))
        cands = []
        for r, c, n in self.candidates(obs, info):
            dy, dp = self.offset(r, c, h, w)
            d = _direction(pose[3] + dy, pose[4] + dp)
            if any(np.linalg.norm(eye - e) < REJECT_M and d @ dd > cos_rej for e, dd in self.rejected):
                continue
            cands.append((r, c, dy, dp, d))
        act = np.zeros(7, np.float32)
        centred = [x for x in cands if abs(x[1] - w / 2) < CENTRE * w and abs(x[0] - h / 2) < CENTRE * h]
        if centred or (cands and self.aim >= MAX_AIM):
            self.pending, self.aim = (eye.copy(), (centred or cands)[0][4]), 0
            act[6] = 1.0                                     # declare
            return act
        if cands:
            act[3], act[4] = np.clip(cands[0][2], -45, 45), np.clip(cands[0][3], -30, 30)
            self.aim += 1
            return act
        self.aim = 0
        self.turns += 1
        if self.turns % TURNS_PER_LAP == 0:
            act[0] = FORWARD_M
        else:
            act[3] = SEARCH_TURN_DEG
        act[4] = np.clip(-pose[4], -30, 30)                  # level the camera while searching
        return act


class LookAround(Searcher):
    """Label-free: local anomalies of the reading."""
    def candidates(self, obs, info):
        return blobs(anomaly_mask(obs["thermal"]), MIN_BLOB)


class Privileged(Searcher):
    """Upper bound: the same search, but the target is the fault mask from the environment's labels."""
    def candidates(self, obs, info):
        m = info.get("fault")
        if m is None or m.sum() < 9:
            return []
        ys, xs = np.nonzero(m)
        return [(float(ys.mean()), float(xs.mean()), int(m.sum()))]


_NETS = {}


def detector(cond="real"):
    """(network, threshold) of E3's first-seed detector trained on `cond` (real: case_studies/detector_model.pt; true and
    ideal: case_studies/detector/model_{cond}_s0.pt, written by detector_train.py), with its validation threshold."""
    if cond not in _NETS:
        import torch
        sys.path.insert(0, str(ROOT / "case_studies"))
        from detector_train import UNet
        res = _detector_results()
        seed = res["agent_detector"]["seed"]
        path = ROOT / "case_studies" / ("detector_model.pt" if cond == "real" else f"detector/model_{cond}_s{seed}.pt")
        m = UNet()
        m.load_state_dict(torch.load(path, map_location="cpu"))
        m.eval()
        _NETS[cond] = (m, res["conditions"][cond][seed]["test_real"]["threshold"])
    return _NETS[cond]


def learned_prob(img, cond="real"):
    """Defect probability per pixel of a reading (same input scaling as in training)."""
    m, _ = detector(cond)                                    # also puts case_studies on the import path
    import torch
    from detector_train import normalise
    x = normalise(img[None].astype(np.float32))
    with torch.no_grad():
        return torch.sigmoid(m(torch.from_numpy(x[:, None]))).numpy()[0, 0]


def _detector_results():
    return json.loads((ROOT / "case_studies" / "detector_results.json").read_text(encoding="utf-8"))


class HotSpot(Searcher):
    """The same search, steered by E3's better label-free detector: hot or cold spots (kelvin from the image median)."""
    thr = None

    def candidates(self, obs, info):
        if HotSpot.thr is None:
            HotSpot.thr = _detector_results()["baselines"]["global hot/cold spot (kelvin from image median)"]["threshold"]
        img = obs["thermal"]
        return blobs(np.abs(img - np.median(img)) >= HotSpot.thr, self.min_px)


class Learned(Searcher):
    """The same search, steered by the detector trained only in thermoscene (on realistic readings)."""
    cond = "real"

    def candidates(self, obs, info):
        _, thr = detector(self.cond)                         # threshold chosen on the detector's validation buildings
        return blobs(learned_prob(obs["thermal"], self.cond) >= thr, self.min_px)


class LearnedTrue(Learned):
    """The same network trained on the true temperature fields of the same views (E3), on the robot's readings."""
    cond = "true"


class LearnedIdeal(Learned):
    """The same network trained on idealised images of the same views (E3), on the robot's readings."""
    cond = "ideal"


AGENTS = {"look-around": LookAround, "hot-spot": HotSpot, "learned-true": LearnedTrue, "learned-ideal": LearnedIdeal,
          "learned": Learned, "privileged": Privileged}
ORDER = ("random", "look-around", "hot-spot", "learned-true", "learned-ideal", "learned", "privileged")
LABEL = {"random": "random", "look-around": "label-free, local anomaly", "hot-spot": "label-free, hot/cold spot",
         "learned-true": "detector trained on true temperature fields", "learned-ideal": "detector trained on idealised images",
         "learned": "learned detector (trained in thermoscene)", "privileged": "privileged (fault labels)"}
CASES = {"B": ("B_hvac_system", "Phoenix office"), "C": ("C_doe_medium_office", "Chicago top-floor perimeter office")}


def run(policy_name, env, rng):
    succ, steps, false_alarms, per_episode = 0, [], 0, []
    for ep in range(EPISODES):
        obs, info = env.reset(seed=int(rng.integers(1 << 30)), options=dict(time=int(rng.integers(env.world.n))))
        pol = AGENTS[policy_name](env.camera) if policy_name in AGENTS else None   # this episode's camera unit
        found, fa, n = False, 0, STEPS
        for t in range(STEPS):
            a = pol(obs, info) if pol else env.action_space.sample()
            obs, r, term, trunc, info = env.step(a)
            if info.get("declared") and not info.get("new_found"):
                fa += 1
            if info.get("collided") and pol:
                pol.turns = 1                     # blocked: turn before trying again
            if term:
                found, n = True, t + 1
                break
            if trunc:
                n = t + 1
                break
        succ += found; false_alarms += fa
        if found:
            steps.append(n)
        per_episode.append(dict(found=found, steps=n, false_declarations=fa))
    return dict(episodes=EPISODES, max_steps=STEPS, success=succ, success_rate=round(succ / EPISODES, 2),
                success_95ci=wilson(succ, EPISODES), median_steps_to_success=float(np.median(steps)) if steps else None,
                false_alarms_per_episode=round(false_alarms / EPISODES, 2), per_episode=per_episode)


def wilson(k, n, z=1.96):
    """95 % Wilson score interval of a success rate."""
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, c - h), 2), round(min(1.0, c + h), 2)]


def mcnemar(a, b):
    """Exact two-sided McNemar test on paired episodes: (episodes only a found, only b found, p-value)."""
    from scipy.stats import binomtest
    fa = [e["found"] for e in a["per_episode"]]
    fb = [e["found"] for e in b["per_episode"]]
    only_a = sum(x and not y for x, y in zip(fa, fb))
    only_b = sum(y and not x for x, y in zip(fa, fb))
    p = binomtest(only_a, only_a + only_b, 0.5).pvalue if only_a + only_b else 1.0
    return only_a, only_b, round(float(p), 4)


PERCEPTION_VIEWS = 60


def perception_check(key, n=PERCEPTION_VIEWS):
    """Seeing versus finding: E3's scoring of every detector on n views of this building's void made as E3's test views
    were (aimed at the void from 1.5-4.5 m at about eye height with the void off-centre, a random time step, a random
    unit of the 160 x 120 class)."""
    sys.path.insert(0, str(ROOT / "case_studies"))
    from detector_data import aim, contrasts, to_grid
    from detector_train import ACTIVE_K, MIN_PX, global_anomaly, instances
    case = CASES[key][0]
    w = ThermalWorld(ROOT / "case_studies" / case / "output" / f"{case}.usda")
    space = w.space_path("Zone")
    voids = [k for k in range(w.np) if w.fault[k] and w.space_of[k] == space]
    rng = np.random.default_rng(5)
    tex = int(rng.integers(1 << 30))
    R, Y, IGN, META = [], [], [], []
    while len(R) < n:
        k = int(rng.choice(voids))
        cam = ThermalCamera.preset("lepton35", seed=int(rng.integers(1 << 30)), supersample=1).randomized(rng)
        pose, _ = aim(w, k, rng, (1.5, 4.5), cam.hfov, space)
        if pose is None:
            continue
        tc = int(rng.integers(w.n))
        f = w.observe(pose, tc, cam, space=space, rng=rng, frame_seed=int(rng.integers(1 << 30)), texture_seed=tex)
        ct = contrasts(f, f)[0]
        m = to_grid(f.fault.astype(float)) >= 0.5
        active = ct is not None and abs(ct) >= ACTIVE_K
        R.append(to_grid(f.reading)); Y.append(m & active); IGN.append(m & (not active))
        META.append(dict(contrast_true_K=ct))
    R, Y, IGN = np.array(R, np.float32), np.array(Y), np.array(IGN)
    res = dict(views=n, views_with_active_void=int(sum(y.sum() >= MIN_PX for y in Y)))
    keep = ("voids", "recall", "precision", "f1", "recall_by_true_contrast")
    for cond in ("true", "ideal", "real"):
        _, thr = detector(cond)
        P = np.stack([learned_prob(r, cond) for r in R])
        res[cond] = {k: v for k, v in instances(P, Y, IGN, META, thr=thr).items() if k in keep}
    thr = _detector_results()["baselines"]["global hot/cold spot (kelvin from image median)"]["threshold"]
    res["hot-spot"] = {k: v for k, v in instances(global_anomaly(R), Y, IGN, META, thr=thr).items() if k in keep}
    return res


def main(cases=("B", "C"), agents=ORDER, perception=False):
    path = ROOT / "case_studies" / "env_baselines.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    pc = out.get("perception_check", {})
    out = {k: v for k, v in out.items() if k in CASES and all("per_episode" in r for n, r in v.items() if n != "building")}
    if pc:
        out["perception_check"] = pc
    if perception:                                   # only the perception check; the agents' results are kept
        for key in cases:
            out.setdefault("perception_check", {})[key] = perception_check(key)
            print(key, "perception", out["perception_check"][key], flush=True)
        det = _detector_results()
        out["learned_threshold"] = det["agent_detector"]["threshold"]
        out["hot_spot_threshold_K"] = det["baselines"]["global hot/cold spot (kelvin from image median)"]["threshold"]
        old = json.loads(path.read_text(encoding="utf-8"))
        if "seconds_per_step" in old:
            out["seconds_per_step"] = old["seconds_per_step"]
        path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        write_md(out)
        return
    t0 = time.time()
    n_steps = 0
    for key in cases:
        case, desc = CASES[key]
        w = ThermalWorld(ROOT / "case_studies" / case / "output" / f"{case}.usda")
        cam = ThermalCamera.preset("lepton35", supersample=1)
        envs = {m: InspectionEnv(w, camera=cam, space="Zone", task="fault_search", max_steps=STEPS,
                                 randomize_camera=True, action_mode=m) for m in ("discrete", "continuous")}
        out.setdefault(key, {})["building"] = desc
        for name in agents:
            rng = np.random.default_rng(11)                       # the same episodes for every agent
            out[key][name] = run(name, envs["continuous" if name in AGENTS else "discrete"], rng)
            n_steps += sum(e["steps"] for e in out[key][name]["per_episode"])
            print(key, name, {k: v for k, v in out[key][name].items() if k != "per_episode"}, flush=True)
    out["seconds_per_step"] = round((time.time() - t0) / max(n_steps, 1), 3)
    det = _detector_results()
    out["learned_threshold"] = det["agent_detector"]["threshold"]
    out["hot_spot_threshold_K"] = det["baselines"]["global hot/cold spot (kelvin from image median)"]["threshold"]
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_md(out)


def write_md(out):
    keys = [k for k in CASES if k in out]
    names = [n for n in ORDER if all(n in out[k] for k in keys)]
    L = ["# E4: agents in the inspection environment (fault search: the wall void of cases B and C)", "",
         f"{EPISODES} episodes of at most {STEPS} steps per agent and building; random start pose, time step and camera "
         f"unit (160 x 120 class, one sample per pixel), the same episodes for every agent. Every detector steers the "
         f"same search: declare a centred candidate, otherwise turn and tilt to centre the largest one (declaring after "
         f"at most {MAX_AIM} corrections), otherwise turn {SEARCH_TURN_DEG:.0f} degrees and move forward after a full "
         f"turn; directions already declared without a fault are skipped. The learned agent's detector "
         f"was trained only on thermoscene scenes of other buildings (`docs/detector.md`, threshold "
         f"{out.get('learned_threshold')}); the hot/cold-spot agent uses the better label-free detector of that "
         f"experiment (threshold {out.get('hot_spot_threshold_K')} K). The agents with detectors trained on true "
         f"temperature fields or on idealised images use the same network trained in that experiment on those forms of "
         f"the same views (first seed, their own validation thresholds), fed the same camera readings. "
         f"Generated by `case_studies/env_baselines.py`.", "", "## Faults found and false declarations", "",
         "| Building | Agent | Faults found (episodes) | 95 % interval of the success rate | Median steps to success "
         "| False declarations per episode |",
         "|---|---|---|---|---|---|"]
    for key in keys:
        for name in names:
            r = out[key][name]
            ci = r.get("success_95ci")
            L.append(f"| {key}: {out[key]['building']} | {LABEL[name]} | {r['success']} of {r['episodes']} | "
                     f"{f'{ci[0]:.2f}-{ci[1]:.2f}' if ci else '-'} | {r['median_steps_to_success']} | "
                     f"{r['false_alarms_per_episode']} |")
    if "learned" in names:
        L += ["", "## Paired comparison with the learned agent", "",
              "Paired comparison with the learned agent on the same episodes (exact McNemar test):", "",
              "| Building | Other agent | Found only by the learned agent | Found only by the other agent | p |",
              "|---|---|---|---|---|"]
        for key in keys:
            for name in names:
                if name != "learned":
                    a, b, p = mcnemar(out[key]["learned"], out[key][name])
                    L.append(f"| {key} | {LABEL[name]} | {a} | {b} | {p} |")
    pc = out.get("perception_check", {})
    if pc:
        L += ["", "## Seeing versus finding", "", f"Seeing versus finding: the detectors scored as in E3 on {PERCEPTION_VIEWS} views aimed at each "
                  f"building's void (1.5-4.5 m, random time step, random 160 x 120 unit; voids with a true contrast below "
                  f"0.5 K ignored):", "",
              "| Building | Detector | Active voids in view | Recall | Precision |", "|---|---|---|---|---|"]
        names_pc = {"true": "trained on true temperature fields", "ideal": "trained on idealised images",
                    "real": "trained on realistic readings (thermoscene)", "hot-spot": "label-free, hot/cold spot"}
        for key in keys:
            for c, nm in names_pc.items():
                if key in pc and c in pc[key]:
                    r = pc[key][c]
                    L.append(f"| {key} | {nm} | {r['voids']} | {r['recall']} | {r['precision']} |")
    if "seconds_per_step" in out:
        L += ["", "## Notes", "", f"About {out['seconds_per_step']} s per environment step on one laptop core (wall-clock, other jobs "
              f"running)."]
    L += ["", "A first version of the search turned in fixed 30-degree steps toward the largest blob and looked away for "
              "three steps after a false declaration. It swung between two headings when a target lay between them and "
              "missed the void when it came into view right after a false declaration, so that even the agent given the "
              "labels found the void of case B in only 12 of 30 episodes. It was replaced by the search described above, "
              "and all agents were re-run on the same episodes; the first results are superseded."]
    (ROOT / "docs" / "env_baselines.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", nargs="+", default=list(ORDER), choices=list(ORDER))
    ap.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    ap.add_argument("--report", action="store_true", help="only rewrite docs/env_baselines.md from the JSON file")
    ap.add_argument("--perception", action="store_true",
                    help="only the perception check (detectors on views aimed at each case's void); agents are kept")
    a = ap.parse_args()
    if a.report:
        write_md(json.loads((ROOT / "case_studies" / "env_baselines.json").read_text(encoding="utf-8")))
    else:
        main(tuple(a.cases), tuple(a.agents), perception=a.perception)
