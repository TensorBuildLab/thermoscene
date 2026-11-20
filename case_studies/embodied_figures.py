"""Figures of the embodied-inspection paper (2026-11 revision). Run after building A, B, C and running
corpus_survey.py, measurement_gap.py and inspection_timing.py:

    python case_studies/embodied_figures.py [name ...]      -> docs/paper/figures/emb_*.png / .pdf
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "case_studies"))
from paper_figures import PROV_COLORS, box_size, canvas, connect, draw_box  # noqa: E402
from thermoscene.embodied import ThermalCamera, ThermalWorld  # noqa: E402
from thermoscene.render.shading import LEVELS  # noqa: E402

OUT = ROOT / "docs" / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
CS = ROOT / "case_studies"
plt.rcParams.update({"font.family": ["Liberation Sans", "Arial", "DejaVu Sans"], "font.size": 8, "axes.titlesize": 8,
                     "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7, "figure.dpi": 150})


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=600 if name.startswith(("emb_obs", "emb_realism", "emb_gallery")) else 300)
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)


def world(case):
    return ThermalWorld(CS / case / "output" / f"{case}.usda")


def lim(a, lo=1, hi=99.5):
    a = a[np.isfinite(a)]
    return float(np.percentile(a, lo)), float(np.percentile(a, hi))


# ------------------------------------------------------------------------------------------------ Fig. 1
def fig_platform():
    W = 7.4
    ts, bs = 7.4, 6.2
    top = [("EnergyPlus\nmodel", "geometry, HVAC\nobjects and nodes,\npeople, lights,\nequipment, weather", "#fefcbf"),
           ("1  Simulate", "surface, zone and\nnode temperatures,\nflows, occupants,\npower (cached)", "#fefcbf"),
           ("2  Trace", "equipment serving\nthe zone, from the\nnode graph,\nwithout names", "#ebf8ff"),
           ("3  Generate", "ducts, pipes, AHU,\nplant; occupants,\nluminaires, desks\n(explicit rules)", "#ebf8ff"),
           ("4  Thermal\nstate", "every temperature\nsimulated or modelled,\nwith its provenance\nlevel", "#ebf8ff"),
           ("5  Image\nformation", "radiometry, angular\nemissivity, reflections,\ncamera model", "#fff5f5")]
    bot = [("Inspection agent", "robot, drone or crawler:\nwhere to go, where to\nlook, when, what it means", "#faf5ff"),
           ("Agent environment", "reset / step: move, look,\nwait, declare; rewards\n(Gymnasium API)", "#f0fff4"),
           ("Thermal world", "OpenUSD scene + RDF\nobserve(pose, time,\ncamera) → frame", "#f0fff4"),
           ("Labelled dataset", "radiometric images, true\ntemperature, instances,\nprovenance (COCO)", "#f0fff4"),]
    fig, ax = canvas(W, 3.0)
    st = [box_size(ax, a, b, ts, bs) for a, b, _ in top]
    sb = [box_size(ax, a, b, ts, bs) for a, b, _ in bot]
    bw, bh = max(s[0] for s in st) + 0.03, max(s[1] for s in st)
    bh2 = max(s[1] for s in sb)
    M, vgap = 0.06, 0.55
    H = M + bh + vgap + bh2 + M
    plt.close(fig); fig, ax = canvas(W, H)
    gap = (W - 2 * M - 6 * bw) / 5
    yt = H - M - bh / 2
    T = [draw_box(ax, M + bw / 2 + i * (bw + gap), yt, bw, bh, a, b, fc=c, ts=ts, bs=bs) for i, (a, b, c) in enumerate(top)]
    for x, y in zip(T[:-1], T[1:]):
        connect(ax, x, y)
    ws = [s[0] + 0.04 for s in sb]
    g2 = (W - 2 * M - sum(ws)) / 3
    yb = M + bh2 / 2
    B, x = [], M
    for (a, b, c), wdt in zip(bot, ws):
        B.append(draw_box(ax, x + wdt / 2, yb, wdt, bh2, a, b, fc=c, ts=ts, bs=bs)); x += wdt + g2
    connect(ax, T[-1], B[2])
    connect(ax, B[2], B[1]); connect(ax, B[2], B[3])
    e, g = B[1], B[0]
    for dy, (p, q), lab in ((0.12, (e, g), "observation"), (-0.12, (g, e), "action")):
        x0 = p[0] + np.sign(q[0] - p[0]) * p[2] / 2; x1 = q[0] - np.sign(q[0] - p[0]) * q[2] / 2
        ax.add_patch(FancyArrowPatch((x0, p[1] + dy), (x1, q[1] + dy), arrowstyle="-|>", mutation_scale=9, color="#2d3748", lw=0.9))
        ax.text((x0 + x1) / 2, p[1] + dy + (0.05 if dy > 0 else -0.05), lab, ha="center", va="bottom" if dy > 0 else "top",
                fontsize=6.0, style="italic", color="#2d3748")
    save(fig, "emb_fig1_platform")


# ------------------------------------------------------------------------------------------------ Fig. 3
def fig_observation():
    """One observation and its labels: case C perimeter office, summer afternoon, occupied."""
    w = world("C_doe_medium_office")
    pose, (tx, ty), _, space = w.camera_poses()["DiffuserCamera"]
    tc = w.time_index("SUMMER 14:00")
    cam = ThermalCamera.preset("handheld_320", seed=11, hfov_deg=float(np.degrees(2 * np.arctan(tx))))
    f = w.observe(pose, tc, cam, space=space, rng=np.random.default_rng(3))
    fig, axs = plt.subplots(2, 3, figsize=(7.4, 3.75))
    lo, hi = lim(f.reading)
    panels = [("(a) Camera reading (°C)", f.reading, "inferno", (lo, hi)),
              ("(b) True surface temperature (°C)", f.true, "inferno", (lo, hi)),
              ("(c) Reading − true (K)", f.reading - f.true, "RdBu_r", (-3, 3))]
    for ax, (t, a, cm, (vmin, vmax)) in zip(axs[0], panels):
        im = ax.imshow(a, cmap=cm, vmin=vmin, vmax=vmax); ax.set_title(t, loc="left"); ax.axis("off")
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cats = [c for c in np.unique(f.category) if c >= 0]
    pal = plt.get_cmap("tab20")
    cat_img = np.full(f.category.shape + (3,), 0.95)
    handles = []
    for i, c in enumerate(cats):
        cat_img[f.category == c] = pal(i % 20)[:3]
        handles.append(Patch(color=pal(i % 20), label=w.categories[c]))
    ax = axs[1, 0]; ax.imshow(cat_img); ax.set_title("(d) Semantic category", loc="left"); ax.axis("off")
    ax.legend(handles=handles, fontsize=5.2, ncol=2, loc="upper left", bbox_to_anchor=(0.0, -0.01), frameon=False,
              handlelength=0.8, columnspacing=0.6)
    names = LEVELS + ["background"]
    pv = np.zeros(f.provenance.shape + (3,))
    for i, n in enumerate(names):
        pv[f.provenance == i] = matplotlib.colors.to_rgb(PROV_COLORS[n])
    ax = axs[1, 1]; ax.imshow(pv); ax.set_title("(e) Provenance of the true temperature", loc="left"); ax.axis("off")
    present = [n for i, n in enumerate(names) if np.any(f.provenance == i)]
    ax.legend(handles=[Patch(color=PROV_COLORS[n], label=n.replace("_", " ")) for n in present], fontsize=5.2, ncol=2,
              loc="upper left", bbox_to_anchor=(0.0, -0.01), frameon=False, handlelength=0.8)
    ax = axs[1, 2]
    im = ax.imshow(np.where(np.isfinite(f.depth), f.depth, np.nan), cmap="viridis"); ax.axis("off")
    ax.set_title("(f) Depth (m)", loc="left"); fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    fig.subplots_adjust(left=0.01, right=0.97, top=0.94, bottom=0.17, wspace=0.12, hspace=0.25)
    save(fig, "emb_obs_fig3")
    return dict(time=w.labels[tc], median_abs_error=float(np.nanmedian(np.abs(f.reading - f.true))))


# ------------------------------------------------------------------------------------------------ Fig. 4
def fig_realism():
    """From the temperature field to what a camera reads: wall void (B, winter morning) and duct gap (B, summer)."""
    w = world("B_hvac_system")
    clean = dict(angular_emissivity=False, specular_reflections=False, texture=dict(enabled=False), atmosphere_extinction_per_m=0.0)
    rows = [("Wall with insulation void", "TS_DEFECT_Insulation_Void", 2.0, "Zone", "WINTER 07:00", None),
            ("Supply duct with insulation gap", "Duct_Insulation_Gap", 1.2, "Plenum", "SUMMER 15:00", None)]
    fig, axs = plt.subplots(2, 5, figsize=(7.4, 2.9))
    ims = []
    for r, (title, tgt, d, sp, lab, _) in enumerate(rows):
        k = w.prim_index(tgt)
        pose = w.view_of(k, d, space=sp)
        tc = w.time_index(lab)
        ideal = ThermalCamera.preset("ideal", resolution=[320, 240], hfov_deg=45.0, supersample=3)
        imgs = [("True temperature", w.observe(pose, tc, ideal, realism=clean).true),
                ("Ideal camera,\nidealised scene", w.observe(pose, tc, ideal, realism=clean).reading),
                ("Ideal camera,\nrealistic scene", w.observe(pose, tc, ideal).reading),
                ("320 × 240 camera\n(random unit)", w.observe(pose, tc, ThermalCamera.preset("handheld_320", seed=5), rng=np.random.default_rng(1)).reading),
                ("160 × 120 camera\n(random unit)", w.observe(pose, tc, ThermalCamera.preset("lepton35", seed=8, hfov_deg=45.0), rng=np.random.default_rng(2)).reading)]
        lo, hi = lim(np.concatenate([a[np.isfinite(a)].ravel() for _, a in imgs]), 2, 98)
        for c, (t, a) in enumerate(imgs):
            ax = axs[r, c]
            im = ax.imshow(a, cmap="inferno", vmin=lo, vmax=hi)
            ax.set_xticks([]); ax.set_yticks([])
            if r == 0:
                ax.set_title(t, fontsize=7)
            if c == 0:
                ax.set_ylabel(f"{title}\n{lab.title()}", fontsize=7)
        ims.append(im)
    fig.subplots_adjust(left=0.07, right=0.92, top=0.88, bottom=0.03, wspace=0.05, hspace=0.08)
    for r, im in enumerate(ims):
        b0, b1 = axs[r, 0].get_position(), axs[r, -1].get_position()
        cax = fig.add_axes([b1.x1 + 0.012, b0.y0, 0.011, b0.height])
        fig.colorbar(im, cax=cax, label="°C")
    save(fig, "emb_realism_fig4")



# ------------------------------------------------------------------------------------------------ Fig. 5
def fig_gap():
    """Detectability against distance (a-c) from measurement_gap.json; the contrast fidelity is in the paper's table."""
    d = json.loads((CS / "measurement_gap.json").read_text(encoding="utf-8"))
    fig, axs = plt.subplots(1, 3, figsize=(7.4, 2.5))
    targets = list(dict.fromkeys(r["target"] for r in d["C_detectability"]))
    colors = {"lepton35": "#d69e2e", "handheld_320": "#3182ce", "drone_640": "#38a169"}
    names = {"lepton35": "160 × 120", "handheld_320": "320 × 240", "drone_640": "640 × 512"}
    tnice = {"envelope void, inside (B)": "(a) Wall void, inside (B)", "duct-insulation gap (B)": "(b) Duct-insulation gap (B)",
             "envelope void, outside (C)": "(c) Wall void, outside (C)"}
    k = d.get("k_ccr", 3.0)
    for i, tg in enumerate(targets[:3]):
        ax = axs[i]
        rr = [r for r in d["C_detectability"] if r["target"] == tg]
        dist = [r["distance_m"] for r in rr]
        for p in d["presets"]:
            ax.plot(dist, [r[p]["ccr_median"] for r in rr], "-o", ms=3, lw=1.1, color=colors.get(p), label=names.get(p, p))
        ax.axhline(k, color="#c53030", lw=0.9, ls="--")
        ax.text(dist[-1], k + 0.4, "detection threshold", ha="right", fontsize=6.5, color="#c53030")
        ax.set_ylim(0, 16); ax.set_xlabel("distance (m)")
        ax.set_title(tnice.get(tg, tg), loc="left", fontsize=8)
        ax.set_ylabel("contrast / variation\nof surroundings")
        ax.grid(alpha=0.25, lw=0.5)
    axs[0].legend(frameon=False, fontsize=6.8, loc="upper right", title="camera class", title_fontsize=6.8)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.88, bottom=0.2, wspace=0.42)
    save(fig, "emb_fig5_gap")


# ------------------------------------------------------------------------------------------------ Fig. 6
def fig_timing():
    d = json.loads((CS / "inspection_timing.json").read_text(encoding="utf-8"))
    grids = d["hour_month_detect"]
    keys = list(grids)
    titles = {0: "(a) Wall void, Phoenix (B), 2 m", 1: "(b) Wall void, Chicago (C), 2 m", 2: "(c) Duct-insulation gap, Phoenix (B), 1.2 m"}
    fig, axs = plt.subplots(1, 3, figsize=(7.4, 2.35))
    for i, k in enumerate(keys):
        ax = axs[i]
        im = ax.imshow(np.array(grids[k], float) * 100, aspect="auto", cmap="viridis", vmin=0, vmax=100, origin="upper",
                       extent=[0, 24, 12.5, 0.5])
        ax.set_xticks([0, 6, 12, 18, 24]); ax.set_yticks([1, 4, 7, 10]); ax.set_yticklabels(["Jan", "Apr", "Jul", "Oct"])
        ax.set_xlabel("hour of day"); ax.set_title(titles.get(i, k), loc="left", fontsize=7)
    fig.subplots_adjust(left=0.06, right=0.88, top=0.88, bottom=0.2, wspace=0.28)
    cax = fig.add_axes([0.9, 0.2, 0.012, 0.68])
    cb = fig.colorbar(im, cax=cax); cb.set_label("time steps with the\nfault detectable (%)")
    save(fig, "emb_fig6_timing")
    for i, k in enumerate(keys):                     # one file per graph (journal rule: graphs not combined)
        fig, ax = plt.subplots(figsize=(3.0, 2.35))
        im = ax.imshow(np.array(grids[k], float) * 100, aspect="auto", cmap="viridis", vmin=0, vmax=100, origin="upper",
                       extent=[0, 24, 12.5, 0.5])
        ax.set_xticks([0, 6, 12, 18, 24]); ax.set_yticks([1, 4, 7, 10]); ax.set_yticklabels(["Jan", "Apr", "Jul", "Oct"])
        ax.set_xlabel("hour of day"); ax.set_title(titles.get(i, k), loc="left", fontsize=7)
        cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03); cb.set_label("time steps with the\nfault detectable (%)")
        fig.tight_layout()
        save(fig, f"emb_fig6_timing_{'abc'[i]}")


# ------------------------------------------------------------------------------------------------ Fig. 2
def fig_gallery(usds=None, out_name="emb_gallery_fig2", ncols=4):
    """Scenes built without configuration from EnergyPlus example models: one realistic view per model."""
    usds = usds or json.loads((CS / "gallery.json").read_text(encoding="utf-8"))
    n = len(usds); rows = int(np.ceil(n / ncols))
    fig, axs = plt.subplots(rows, ncols, figsize=(7.4, 1.55 * rows + 0.1), squeeze=False)
    for ax, item in zip(axs.ravel(), usds):
        w = ThermalWorld(CS / item["usd"])          # scenes from `thermoscene auto` (case_studies/gallery/, not in git)
        cams = w.camera_poses()
        name = item.get("camera") or next(iter(cams))
        pose, (tx, ty), _, space = cams[name]
        tc = w.time_index(item.get("frame") or "SUMMER 15:00")
        cam = ThermalCamera.preset("handheld_320", seed=len(item["title"]), hfov_deg=float(np.degrees(2 * np.arctan(tx))))
        f = w.observe(pose, tc, cam, space=space, rng=np.random.default_rng(0))
        lo, hi = lim(f.reading, 1, 99)
        ax.imshow(f.reading, cmap="inferno", vmin=lo, vmax=hi)
        ax.set_title(item["title"], fontsize=6.0, pad=2); ax.axis("off")
    for ax in axs.ravel()[n:]:
        ax.axis("off")
    fig.subplots_adjust(left=0.005, right=0.995, top=0.95, bottom=0.005, wspace=0.03, hspace=0.18)
    save(fig, out_name)


# ------------------------------------------------------------------------------------------------ graphical abstract
def fig_graphical_abstract():
    """Landscape graphical abstract (8 x 3.2 in, readable at 13 cm wide): energy model -> labelled realistic thermal
    world -> what an inspection agent learns from it."""
    w = world("C_doe_medium_office")
    pose, (tx, ty), _, space = w.camera_poses()["DiffuserCamera"]
    tc = w.time_index("SUMMER 14:00")
    cam = ThermalCamera.preset("handheld_320", seed=11, hfov_deg=float(np.degrees(2 * np.arctan(tx))))
    f = w.observe(pose, tc, cam, space=space, rng=np.random.default_rng(3))
    gap = json.loads((CS / "measurement_gap.json").read_text(encoding="utf-8"))
    tim = json.loads((CS / "inspection_timing.json").read_text(encoding="utf-8"))
    duct = next(r for r in gap["B_contrasts"] if r["pair"].startswith("duct") and r["frame"].startswith("SUMMER"))
    kept = 100 * duct["realistic_mean_K"] / duct["true_K"]
    g = tim["B duct-insulation gap (Phoenix), 1.2 m"]
    anyt = g["any time"]["p_detect"]; inf = g["model-informed: flowing, supply-plenum difference in its top 20 %"]["p_detect"]
    W, H = 8.0, 3.4
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    fig.text(0.5, 0.93, "Energy models as labelled thermal worlds for inspection robots", ha="center", va="center",
             fontsize=14, weight="bold", color="#1a202c")
    bt, bb = "EnergyPlus\nmodel", "simulated\ntemperatures,\nHVAC, people"
    bw_, bh_ = box_size(ax, bt, bb, 12.5, 11)
    draw_box(ax, 0.8, 1.55, bw_, bh_, bt, bb, fc="#fefcbf", ts=12.5, bs=11)
    x_arrow = 0.8 + bw_ / 2 + 0.05
    ax.add_patch(FancyArrowPatch((x_arrow, 1.55), (x_arrow + 0.3, 1.55), arrowstyle="-|>", mutation_scale=16,
                                 color="#2d3748", lw=1.6))
    lo, hi = lim(f.reading)
    a1 = fig.add_axes([0.235, 0.13, 0.215, 0.62])
    a1.imshow(f.reading, cmap="inferno", vmin=lo, vmax=hi, interpolation="none"); a1.axis("off")
    a1.set_title("camera reading", fontsize=11.5, pad=4)
    a2 = fig.add_axes([0.46, 0.13, 0.215, 0.62])
    a2.imshow(f.true, cmap="inferno", vmin=lo, vmax=hi, interpolation="none"); a2.axis("off")
    a2.set_title("true temperature (label)", fontsize=11.5, pad=4)
    x0 = 5.5
    items = [("Reading ≠ temperature", f"a duct-wrap gap keeps only\n{kept:.0f} % of its contrast"),
             ("When to look", f"duct gap: {anyt:.0f} % → {inf:.0f} % detected\nwhen timed by the model"),
             ("What it costs", "steadiest fault signature,\n1 % of a year's cooling")]
    for i, (tt, b) in enumerate(items):
        y = 2.42 - i * 0.86
        ax.text(x0, y + 0.19, tt, fontsize=12, weight="bold", color="#1a202c", va="center")
        ax.text(x0, y - 0.17, b, fontsize=11, color="#2d3748", va="center", linespacing=1.15)
    save(fig, "emb_graphical_abstract")


FIGS = {"platform": fig_platform, "observation": fig_observation, "realism": fig_realism, "gap": fig_gap,
        "timing": fig_timing, "gallery": fig_gallery, "graphical_abstract": fig_graphical_abstract}

if __name__ == "__main__":
    for name in (sys.argv[1:] or FIGS):
        print(name, FIGS[name]() or "")
