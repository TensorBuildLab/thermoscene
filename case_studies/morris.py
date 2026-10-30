"""Global screening (Morris elementary effects) of the apparent contrasts of case B.

Same 15 parameters, ranges and 8 contrasts as case_studies/sensitivity.py, but every parameter moves along random
trajectories through the whole parameter space (r trajectories, p-level grid, step = p / (2 (p - 1)) of the range),
so interactions and non-linear effects show up as a spread (sigma) of the elementary effects.
mu* = mean |elementary effect| is the typical change of a contrast (K) when the parameter moves over its full range.
Scenes are rebuilt with the EnergyPlus run cached; camera settings need no rebuild.
Writes case_studies/morris.json and docs/morris.md.  Run:  python case_studies/morris.py [r]
"""
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene import pipeline  # noqa: E402

sys.path.insert(0, str(ROOT / "case_studies"))
from sensitivity import CASE, METRICS, PARAMS, metrics, nest  # noqa: E402

P_LEVELS = 4
DELTA = P_LEVELS / (2 * (P_LEVELS - 1))


def trajectories(k, r, rng):
    out = []
    for _ in range(r):
        x = rng.choice(np.arange(P_LEVELS - 1) / (P_LEVELS - 1), size=k)
        x = np.where(x + DELTA <= 1 + 1e-9, x, x - DELTA)
        pts, order = [x.copy()], rng.permutation(k)
        for i in order:
            x = x.copy()
            x[i] = x[i] + DELTA if x[i] + DELTA <= 1 + 1e-9 else x[i] - DELTA
            pts.append(x)
        out.append((order, pts))
    return out


def value(i, u):
    _, _, lo, hi, _ = PARAMS[i]
    return lo + u * (hi - lo)


def main():
    logging.basicConfig(level=logging.WARNING)
    r = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    rng = np.random.default_rng(2026)
    k = len(PARAMS)
    keys = [m for m, *_ in METRICS]
    work = ROOT / "case_studies" / "B_hvac_system" / "morris"
    cache = {}

    def evaluate(u):
        overrides, cam = {"storage": {"time_series": "sidecar"}, "cameras": []}, [0.95, 20.0]
        for i, (_, path, *_rest) in enumerate(PARAMS):
            v = value(i, u[i])
            if path[0] == "camera":
                cam[0 if path[1] == "emissivity_setting" else 1] = v
            else:
                overrides = _merge(overrides, nest(path, v))
        tag = hashlib.sha1(json.dumps(overrides, sort_keys=True).encode()).hexdigest()[:12]
        if tag not in cache:
            out = work / tag
            usd = pipeline.build(CASE, overrides={**overrides, "output_dir": str(out)})
            cache[tag] = (out, usd)
        return metrics(cache[tag][1], tuple(cam))

    ee = {m: [[] for _ in range(k)] for m in keys}
    for t, (order, pts) in enumerate(trajectories(k, r, rng)):
        f = [evaluate(u) for u in pts]
        for step, i in enumerate(order):
            sign = 1 if pts[step + 1][i] > pts[step][i] else -1
            for m in keys:
                ee[m][i].append(sign * (f[step + 1][m] - f[step][m]) / DELTA)
        print(f"trajectory {t + 1}/{r} done ({len(cache)} scenes built)")
    shutil.rmtree(work, ignore_errors=True)
    res = {m: [dict(parameter=PARAMS[i][0], mu_star=float(np.mean(np.abs(ee[m][i]))), mu=float(np.mean(ee[m][i])),
                    sigma=float(np.std(ee[m][i], ddof=1))) for i in range(k)] for m in keys}
    (ROOT / "case_studies" / "morris.json").write_text(encoding="utf-8", data=json.dumps(
        dict(trajectories=r, levels=P_LEVELS, metrics={m: d for m, d, *_ in METRICS}, results=res), indent=2, ensure_ascii=False))
    lines = ["| Parameter | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for i in range(k):
        cells = []
        for m in keys:
            e = res[m][i]
            cells.append("—" if e["mu_star"] < 0.05 else f"{e['mu_star']:.2f} ({e['sigma']:.2f})")
        lines.append(f"| {PARAMS[i][0]} ({PARAMS[i][2]} – {PARAMS[i][3]}) | " + " | ".join(cells) + " |")
    legend = "\n".join(f"- **{m}**: {d}" for m, d, *_ in METRICS)
    (ROOT / "docs" / "morris.md").write_text(encoding="utf-8", data=
        "# Morris screening of apparent contrasts (case B)\n\n"
        f"{r} trajectories on a {P_LEVELS}-level grid over the parameter ranges of `docs/sensitivity.md`. Cells: mu* (sigma), the "
        "mean absolute and the standard deviation of the elementary effects, in K per full parameter range. "
        "— = mu* below 0.05 K. A sigma comparable to mu* indicates interaction with other parameters or non-linearity.\n\n"
        + legend + "\n\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))


def _merge(a, b):
    out = dict(a)
    for key, v in b.items():
        out[key] = _merge(out[key], v) if isinstance(v, dict) and isinstance(out.get(key), dict) else v
    return out


if __name__ == "__main__":
    main()
