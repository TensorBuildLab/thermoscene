"""Experiment E3: a thermal defect detector trained only in thermoscene, tested on buildings it has never seen.

Data: case_studies/detector_data.py (insulation voids injected into exterior walls of EnergyPlus example buildings of
distinct geometry, views from inside at robot eye height through random units of three camera classes, 120 x 160
pixels; buildings split into train / validation / test).

Labels. A void is a target only while it has a thermal signature: its true surface temperature differs from the wall
around it by at least ACTIVE_K. Void pixels below that are ignored (neither target nor background) in training and
evaluation, because no camera can see them and labelling them would teach a detector to guess.

Detector: a small U-Net that marks void pixels. Its input is what a radiometric camera on a robot provides: each
pixel's temperature reading relative to the image median, in kelvin (divided by 5 K, clipped at +-20 K).

Training conditions (what must a virtual training environment contain?):
  true   the true surface-temperature field (the 'temperature matrix' an energy model gives directly);
  ideal  an ideal camera in an idealised scene (emissivity and diffuse reflection only: a naive simulator);
  real   the camera reading in the realistic scene, random camera units (thermoscene).
Every detector is tested on the realistic readings of the test buildings, what a robot would see. The 'real' detector
is also trained on fewer buildings (learning curve). Baselines without training: a local anomaly detector (pixels
departing from the median of their 15 x 15 neighbourhood, in units of the image's robust noise) and a global hot- and
cold-spot search (pixels departing from the image median, in kelvin).

Thresholds. Each detector's decision threshold is the one in PROB_GRID (each baseline's in its own grid) that
maximises instance F1 on the validation buildings of the detector's own training form, as a developer without field
data would choose it. As a check that the gap is not one of calibration only, every detector is also scored at the
threshold that is best on the realistic test images themselves (an oracle no developer has).

Metrics: void-pixel average precision; with predicted blobs (>= 4 pixels) matched to voids at intersection over union
>= 0.3: instance recall (share of active voids with >= 4 visible pixels that a predicted blob matches), precision
(share of predicted blobs that match an active void; blobs lying mostly on inactive voids are not counted) and F1;
false blobs per view without any void; recall by the void's true contrast.

Models are kept in case_studies/detector/ (model_<form>_s<seed>.pt, model_curve_<n>.pt) and reused when present, so a
re-run only re-scores them; delete them to retrain.

    python case_studies/detector_train.py [--epochs N] [--seeds N] [--quick] [--no-curve] [--report]
Writes case_studies/detector_results.json, case_studies/detector_model.pt (the 'real' detector of seed 0, for the
agent test) and docs/detector.md (--report rewrites only the latter from the JSON file).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from scipy.ndimage import label, median_filter
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "case_studies" / "detector"
SCALE_K, CLIP_K = 5.0, 20.0
ACTIVE_K = 0.5
MIN_PX = 4
IOU_MIN = 0.3
WIDTH = (8, 16, 32, 64)
BINS = ((0.5, 1.0), (1.0, 2.0), (2.0, np.inf))
torch.set_num_threads(8)


# ------------------------------------------------------------------------------------------------ data
def load(tags, kinds=("real", "ideal", "true")):
    """Images by kind, target masks (active voids), ignore masks (inactive voids) and per-view metadata."""
    X = {k: [] for k in kinds}
    Y, IGN, META = [], [], []
    for t in tags:
        z = np.load(DATA / "scenes" / t / "views.npz")
        meta = json.loads((DATA / "scenes" / t / "views.json").read_text(encoding="utf-8"))
        for k in kinds:
            X[k].append(z[k])
        for m, mask in zip(meta, z["mask"]):
            active = m.get("contrast_true_K") is not None and abs(m["contrast_true_K"]) >= ACTIVE_K
            Y.append(mask & active)
            IGN.append(mask & (not active))
            META.append(dict(m, building=t, active=bool(active and mask.sum() >= MIN_PX)))
    return {k: np.concatenate(v) for k, v in X.items()}, np.array(Y), np.array(IGN), META


def normalise(img):
    """Reading relative to the image median, kelvin / SCALE_K, clipped: what a radiometric robot camera gives."""
    x = img - np.median(img.reshape(len(img), -1), axis=1)[:, None, None]
    return (np.clip(x, -CLIP_K, CLIP_K) / SCALE_K).astype(np.float32)


# ------------------------------------------------------------------------------------------------ model
def block(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                         nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class UNet(nn.Module):
    """Three poolings: 120 x 160 -> 15 x 20 at the bottom."""
    def __init__(self, c=WIDTH):
        super().__init__()
        self.enc = nn.ModuleList([block(1, c[0])] + [block(c[i], c[i + 1]) for i in range(len(c) - 1)])
        self.up = nn.ModuleList([nn.ConvTranspose2d(c[i + 1], c[i], 2, stride=2) for i in reversed(range(len(c) - 1))])
        self.dec = nn.ModuleList([block(2 * c[i], c[i]) for i in reversed(range(len(c) - 1))])
        self.head = nn.Conv2d(c[0], 1, 1)

    def forward(self, x):
        skips = []
        for i, e in enumerate(self.enc):
            x = e(x)
            if i < len(self.enc) - 1:
                skips.append(x)
                x = nn.functional.max_pool2d(x, 2)
        for u, d in zip(self.up, self.dec):
            x = u(x)
            s = skips.pop()
            x = d(torch.cat([x[..., :s.shape[-2], :s.shape[-1]], s], 1))
        return self.head(x)


def train(X, Y, IGN, seed, epochs, Xv=None, Yv=None, IGNv=None, log=None, batch=32):
    torch.manual_seed(seed); np.random.seed(seed)
    m = UNet()
    opt = torch.optim.AdamW(m.parameters(), lr=3e-3, weight_decay=1e-4)
    steps = epochs * int(np.ceil(len(X) / batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=steps)
    pos = float(Y.mean())
    pw = torch.tensor(min(5.0, (1 - pos) / max(pos, 1e-4)))
    Xt = torch.from_numpy(X[:, None])
    Yt = torch.from_numpy(Y[:, None].astype(np.float32))
    Wt = torch.from_numpy((~IGN)[:, None].astype(np.float32))
    rng = np.random.default_rng(seed)
    best, best_state = -1.0, None
    for ep in range(epochs):
        m.train()
        order = rng.permutation(len(X))
        tot = 0.0
        for b in range(0, len(order), batch):
            idx = order[b:b + batch]
            xb, yb, wb = Xt[idx].clone(), Yt[idx].clone(), Wt[idx].clone()
            flip = torch.from_numpy(rng.random(len(idx)) < 0.5)
            xb[flip] = xb[flip].flip(-1); yb[flip] = yb[flip].flip(-1); wb[flip] = wb[flip].flip(-1)
            xb = xb * torch.from_numpy(rng.uniform(0.8, 1.25, (len(idx), 1, 1, 1)).astype(np.float32))
            logit = m(xb)
            p = torch.sigmoid(logit) * wb
            bce = nn.functional.binary_cross_entropy_with_logits(logit, yb, weight=wb, pos_weight=pw, reduction="sum")
            bce = bce / wb.sum().clamp(min=1.0)
            dice = 1 - (2 * (p * yb).sum() + 1) / (p.sum() + yb.sum() + 1)
            loss = bce + dice
            opt.zero_grad(); loss.backward(); opt.step(); sched.step()
            tot += float(loss.detach()) * len(idx)
        if Xv is not None:
            ap = pixel_ap(predict(m, Xv), Yv, IGNv)
            if ap > best:
                best, best_state = ap, {k: v.clone() for k, v in m.state_dict().items()}
            if log:
                log(f"    epoch {ep + 1}/{epochs}: loss {tot / len(X):.3f}, validation AP {ap:.3f}")
    if best_state:
        m.load_state_dict(best_state)
    return m


@torch.no_grad()
def predict(m, X):
    m.eval()
    return np.concatenate([torch.sigmoid(m(torch.from_numpy(X[b:b + 64, None]))).numpy()[:, 0]
                           for b in range(0, len(X), 64)])


# ------------------------------------------------------------------------------------------------ metrics
def pixel_ap(P, Y, IGN):
    keep = ~IGN.ravel()
    p, y = P.ravel()[keep], Y.ravel()[keep]
    if y.sum() == 0:
        return float("nan")
    y = y[np.argsort(-p, kind="stable")]
    prec = np.cumsum(y) / np.arange(1, len(y) + 1)
    return float((prec * y).sum() / y.sum())


def blobs(p, thr=0.5):
    lab, n = label(p >= thr)
    if not n:
        return lab, []
    sizes = np.bincount(lab.ravel())[1:]
    return lab, [i + 1 for i, s in enumerate(sizes) if s >= MIN_PX]


def instances(P, Y, IGN, META=None, thr=0.5, iou_min=IOU_MIN):
    """Instance matching by intersection over union (>= iou_min): recall over active voids, precision over predicted
    blobs (a blob lying mostly on an inactive void is not counted), false blobs per view without any void, and recall
    by the void's true contrast."""
    n_gt = n_found = n_pred = n_tp = fp_neg = neg_views = 0
    by_bin = {b: [0, 0] for b in BINS}
    for i, (p, y, ign) in enumerate(zip(P, Y, IGN)):
        gl, ng = label(y)
        gsz = np.bincount(gl.ravel())[1:] if ng else np.array([])
        gt = [j + 1 for j, s in enumerate(gsz) if s >= MIN_PX]
        pl, keep = blobs(p, thr)
        iou = np.zeros((len(gt), len(keep)))
        for a, j in enumerate(gt):
            g = gl == j
            for b, k in enumerate(keep):
                q = pl == k
                inter = np.count_nonzero(g & q)
                if inter:
                    iou[a, b] = inter / np.count_nonzero(g | q)
        found = [bool(len(keep) and iou[a].max() >= iou_min) for a in range(len(gt))]
        n_gt += len(gt); n_found += sum(found)
        if META is not None and gt and META[i].get("contrast_true_K") is not None:
            c = abs(META[i]["contrast_true_K"])
            for b in BINS:
                if b[0] <= c < b[1]:
                    by_bin[b][0] += len(gt); by_bin[b][1] += sum(found)
        empty = not y.any() and not ign.any()
        for b, k in enumerate(keep):
            q = pl == k
            hit = bool(len(gt) and iou[:, b].max() >= iou_min)
            if not hit and np.count_nonzero(q & ign) >= 0.5 * np.count_nonzero(q):
                continue
            n_pred += 1; n_tp += hit
            fp_neg += empty
        neg_views += empty
    rec, prec = n_found / max(n_gt, 1), n_tp / max(n_pred, 1)
    out = dict(voids=n_gt, recall=round(rec, 3), precision=round(prec, 3), f1=round(2 * rec * prec / max(rec + prec, 1e-9), 3),
               false_blobs_per_view_without_void=round(fp_neg / max(neg_views, 1), 3), views_without_void=neg_views)
    if META is not None:
        out["recall_by_true_contrast"] = {f"{a}-{b} K" if np.isfinite(b) else f">= {a} K":
                                          (round(f / n, 3) if n else None) for (a, b), (n, f) in by_bin.items()}
    return out


def local_anomaly(real):
    """Label-free baseline 1: |reading - median of the 15 x 15 neighbourhood| in units of the image's robust noise."""
    out = np.empty_like(real)
    for i, img in enumerate(real):
        res = img - median_filter(img, size=15)
        s = 1.4826 * np.median(np.abs(res - np.median(res)))
        out[i] = np.abs(res) / max(s, 1e-6)
    return out


def global_anomaly(real):
    """Label-free baseline 2 (a thermographer's hot/cold-spot search): |reading - image median| in kelvin."""
    return np.abs(real - np.median(real.reshape(len(real), -1), axis=1)[:, None, None])


BASELINES = {"local anomaly (15 x 15 median, noise units)": (local_anomaly, (2, 3, 4, 6, 8, 12)),
             "global hot/cold spot (kelvin from image median)": (global_anomaly, (0.5, 1, 1.5, 2, 3, 4, 6))}
PROB_GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99)


def trained(path, X, Y, IGN, seed, epochs, Xv, Yv, IGNv, log=None):
    """A trained detector: loaded from path if present (re-scoring only), otherwise trained and saved there."""
    if path.exists():
        m = UNet()
        m.load_state_dict(torch.load(path, map_location="cpu"))
        m.eval()
        return m
    m = train(X, Y, IGN, seed=seed, epochs=epochs, Xv=Xv, Yv=Yv, IGNv=IGNv, log=log)
    torch.save(m.state_dict(), path)
    return m


def best_threshold(S, Y, IGN, grid):
    """Threshold on the score S that maximises instance F1 on validation views."""
    f1 = [instances(S, Y, IGN, thr=t)["f1"] for t in grid]
    return float(grid[int(np.argmax(f1))])


def evaluate(P, Y, IGN, META, thr=0.5):
    r = dict(threshold=thr, pixel_ap=round(pixel_ap(P, Y, IGN), 3), **instances(P, Y, IGN, META, thr=thr))
    by = {}
    for key, f in (("camera", lambda m: m["preset"]),
                   ("distance", lambda m: "1-2.5 m" if m["distance_m"] < 2.5 else ("2.5-3.5 m" if m["distance_m"] < 3.5 else "3.5-4.5 m"))):
        groups = {}
        for i, m in enumerate(META):
            groups.setdefault(f(m), []).append(i)
        by[key] = {g: dict(views=len(ix), **{k: v for k, v in instances(P[ix], Y[ix], IGN[ix], thr=thr).items()})
                   for g, ix in sorted(groups.items())}
    r["by"] = by
    return r


# ------------------------------------------------------------------------------------------------ main
def main(epochs, seeds, quick, curve=True):
    split = json.loads((DATA / "split.json").read_text(encoding="utf-8"))
    t0 = time.time()
    log = lambda s: print(f"[{time.time() - t0:6.0f} s] {s}", flush=True)
    Xtr, Ytr, Itr, Mtr = load(split["train"])
    Xva, Yva, Iva, _ = load(split["val"])
    Xte, Yte, Ite, Mte = load(split["test"])
    log(f"train {len(Ytr)} views ({len(split['train'])} buildings), val {len(Yva)}, test {len(Yte)} "
        f"({len(split['test'])} buildings); active-void pixels {Ytr.mean() * 100:.2f} % of training pixels; "
        f"views with an active void: train {sum(m['active'] for m in Mtr)}, test {sum(m['active'] for m in Mte)}")
    N = {k: normalise(v) for k, v in Xtr.items()}
    NV = {k: normalise(v) for k, v in Xva.items()}
    test_real, test_ideal = normalise(Xte["real"]), normalise(Xte["ideal"])
    ct = [abs(m["contrast_true_K"]) for m in Mte if m.get("contrast_true_K") is not None]
    out = dict(epochs=epochs, seeds=seeds, active_threshold_K=ACTIVE_K, width=list(WIDTH),
               train_buildings=len(split["train"]), val_buildings=len(split["val"]), test_buildings=len(split["test"]),
               train_views=len(Ytr), test_views=len(Yte), test_views_with_active_void=int(sum(m["active"] for m in Mte)),
               test_void_true_contrast_K_p10_p50_p90=np.percentile(ct, [10, 50, 90]).round(2).tolist() if ct else None,
               conditions={})
    out["baselines"] = {}
    for name, (fn, grid) in BASELINES.items():
        thr = best_threshold(fn(Xva["real"]), Yva, Iva, grid)
        out["baselines"][name] = evaluate(fn(Xte["real"]), Yte, Ite, Mte, thr)
        log(f"baseline {name}: {json.dumps({k: v for k, v in out['baselines'][name].items() if k != 'by'})}")
    best_name = max(out["baselines"], key=lambda k: out["baselines"][k]["f1"])
    out["baseline_label_free"] = dict(out["baselines"][best_name], name=best_name)
    best_real = None
    for cond in ("true", "ideal", "real"):
        runs = []
        for s in range(seeds):
            log(f"train on '{cond}', seed {s}")
            m = trained(DATA / f"model_{cond}_s{s}.pt", N[cond], Ytr, Itr, s, epochs, NV[cond], Yva, Iva,
                        log=log if quick else None)
            thr = best_threshold(predict(m, NV[cond]), Yva, Iva, PROB_GRID)      # chosen in the detector's own domain
            P_real = predict(m, test_real)
            r = dict(test_real=evaluate(P_real, Yte, Ite, Mte, thr))
            r["test_ideal"] = {k: v for k, v in evaluate(predict(m, test_ideal), Yte, Ite, Mte, thr).items() if k != "by"}
            o_thr = best_threshold(P_real, Yte, Ite, PROB_GRID)                  # oracle: best on the test images
            r["test_real_oracle_threshold"] = dict(threshold=o_thr, **instances(P_real, Yte, Ite, thr=o_thr))
            t = r["test_real"]
            log(f"  '{cond}' seed {s}: threshold {thr}; realistic test AP {t['pixel_ap']}, F1 {t['f1']}, recall "
                f"{t['recall']}, precision {t['precision']}, false blobs {t['false_blobs_per_view_without_void']}; "
                f"oracle threshold {o_thr}: F1 {r['test_real_oracle_threshold']['f1']}; idealised test AP "
                f"{r['test_ideal']['pixel_ap']}, F1 {r['test_ideal']['f1']}")
            runs.append(r)
            if cond == "real" and s == 0:
                best_real = (thr, m)                     # the agent test uses seed 0 (no selection on test results)
        out["conditions"][cond] = runs
    torch.save(best_real[1].state_dict(), ROOT / "case_studies" / "detector_model.pt")
    out["agent_detector"] = dict(condition="real", seed=0, threshold=best_real[0])
    out["learning_curve"] = []
    if curve:
        rng = np.random.default_rng(0)
        tags = list(split["train"])
        for n in [k for k in (5, 10, 20) if k < len(tags)] + [len(tags)]:
            sub = sorted(rng.choice(tags, n, replace=False).tolist()) if n < len(tags) else tags
            Xs, Ys, Is, _ = load(sub, kinds=("real",))
            ep = int(round(epochs * min(4.0, len(Ytr) / len(Ys))))            # about the same number of updates
            path = DATA / ("model_real_s0.pt" if n == len(tags) else f"model_curve_{n}.pt")   # all buildings = seed 0
            m = trained(path, normalise(Xs["real"]), Ys, Is, 0, ep, NV["real"], Yva, Iva)
            thr = best_threshold(predict(m, NV["real"]), Yva, Iva, PROB_GRID)
            r = evaluate(predict(m, test_real), Yte, Ite, Mte, thr)
            out["learning_curve"].append(dict(buildings=n, views=len(Ys), epochs=ep, threshold=thr, pixel_ap=r["pixel_ap"],
                                              f1=r["f1"], recall=r["recall"], precision=r["precision"]))
            log(f"learning curve: {n} buildings -> AP {r['pixel_ap']}, F1 {r['f1']}")
    (ROOT / "case_studies" / "detector_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_md(out)
    log("done")


def summary(runs, key, sub=None, part="test_real"):
    v = np.array([(r[part][key] if sub is None else r[part][key][sub]) for r in runs], float)
    if np.isnan(v).all():
        return "–"
    return f"{np.nanmean(v):.2f}" + (f" ± {np.nanstd(v, ddof=1):.2f}" if len(v) > 1 else "")


def write_md(o):
    n_all = o["train_buildings"] + o["val_buildings"] + o["test_buildings"]
    p10, p50, p90 = o["test_void_true_contrast_K_p10_p50_p90"]
    L = ["# E3: a defect detector trained only in thermoscene", "",
         f"Insulation voids in exterior walls of {n_all} EnergyPlus example buildings of distinct geometry: "
         f"{o['train_buildings']} for training ({o['train_views']} views), {o['val_buildings']} for validation, "
         f"{o['test_buildings']} unseen buildings for testing ({o['test_views']} views, {o['test_views_with_active_void']} "
         f"with a void whose true contrast is at least {o['active_threshold_K']} K; true contrast of the test voids "
         f"{p10} / {p50} / {p90} K at the 10th / 50th / 90th percentile). Every detector is tested on realistic camera "
         f"readings of the test buildings, with the decision threshold chosen on the validation buildings of its own "
         f"training form. Mean ± standard deviation over {o['seeds']} training seeds. Generated by "
         f"`case_studies/detector_train.py`.", "",
         "| Trained on | Threshold | Void-pixel AP | Instance F1 | Recall | Precision | False blobs per view without a void "
         "| F1 at the oracle threshold |",
         "|---|---|---|---|---|---|---|---|"]
    names = {"true": "true temperature field", "ideal": "ideal camera, idealised scene", "real": "realistic readings (thermoscene)"}
    for c, runs in o["conditions"].items():
        thr = ", ".join(f"{r['test_real']['threshold']:g}" for r in runs)
        orc = summary(runs, "f1", part="test_real_oracle_threshold") if "test_real_oracle_threshold" in runs[0] else "–"
        L.append(f"| {names[c]} | {thr} | {summary(runs, 'pixel_ap')} | {summary(runs, 'f1')} | {summary(runs, 'recall')} | "
                 f"{summary(runs, 'precision')} | {summary(runs, 'false_blobs_per_view_without_void')} | {orc} |")
    for name, b in o["baselines"].items():
        L.append(f"| no training: {name} | {b['threshold']:g} | {b['pixel_ap']} | {b['f1']} | {b['recall']} | "
                 f"{b['precision']} | {b['false_blobs_per_view_without_void']} | – |")
    L += ["", "The oracle threshold is the one that is best on the realistic test images themselves, which no developer "
          "without field data can choose; it shows how much of the gap a better calibration alone could close.", "",
          "## The same detectors on idealised test images", "",
          "| Trained on | Void-pixel AP | Instance F1 | Recall | Precision |", "|---|---|---|---|---|"]
    for c, runs in o["conditions"].items():
        L.append(f"| {names[c]} | {summary(runs, 'pixel_ap', part='test_ideal')} | {summary(runs, 'f1', part='test_ideal')} "
                 f"| {summary(runs, 'recall', part='test_ideal')} | {summary(runs, 'precision', part='test_ideal')} |")
    b = o["baseline_label_free"]
    L += ["", "## Recall by the void's true contrast", "", "| Trained on | " + " | ".join(o["baseline_label_free"]["recall_by_true_contrast"]) + " |",
          "|---|" + "---|" * len(o["baseline_label_free"]["recall_by_true_contrast"])]
    for c, runs in o["conditions"].items():
        L.append(f"| {names[c]} | " + " | ".join(summary(runs, "recall_by_true_contrast", k)
                                                  for k in o["baseline_label_free"]["recall_by_true_contrast"]) + " |")
    L.append("| label-free baseline | " + " | ".join(str(v) for v in b["recall_by_true_contrast"].values()) + " |")
    if o["learning_curve"]:
        L += ["", "## Learning curve (realistic training, one seed)", "",
              "| Training buildings | Views | Epochs | Threshold | AP | F1 | Recall | Precision |", "|---|---|---|---|---|---|---|---|"]
        L += [f"| {r['buildings']} | {r['views']} | {r['epochs']} | {r.get('threshold', '–')} | {r['pixel_ap']} | {r['f1']} | "
              f"{r['recall']} | {r['precision']} |" for r in o["learning_curve"]]
    real = o["conditions"]["real"][0]["test_real"]["by"]
    L += ["", "## Realistic detector by camera class and distance (seed 0)", "", "| Group | Views | F1 | Recall | Precision |",
          "|---|---|---|---|---|"]
    for k, g in real.items():
        L += [f"| {k}: {n} | {v['views']} | {v['f1']} | {v['recall']} | {v['precision']} |" for n, v in g.items()]
    (ROOT / "docs" / "detector.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="print validation AP per epoch")
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--report", action="store_true", help="only rewrite docs/detector.md from detector_results.json")
    a = ap.parse_args()
    if a.report:
        write_md(json.loads((ROOT / "case_studies" / "detector_results.json").read_text(encoding="utf-8")))
    else:
        main(a.epochs, a.seeds, a.quick, curve=not a.no_curve)
