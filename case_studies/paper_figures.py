"""Figures for the paper. Run after building and rendering the case studies:
    python case_studies/paper_figures.py [fig_name ...]   -> docs/paper/figures/*.png
"""
import json
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from pxr import Usd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermoscene.render.cameras import rays_from  # noqa: E402
from thermoscene.render.raycast import LEVELS, accel, raycast, usd_faces  # noqa: E402
from thermoscene.render.shading import Series, labels, thermal_prims  # noqa: E402

OUT = ROOT / "docs" / "paper" / "figures"
CS = ROOT / "case_studies"
plt.rcParams.update({"font.family": ["Liberation Sans", "DejaVu Sans"], "font.size": 9, "axes.titlesize": 9, "figure.dpi": 150})
PROV_COLORS = {"simulated": "#2b6cb0", "physics": "#38a169", "engineering_rule": "#805ad5", "heuristic": "#dd6b20",
               "assumed_layout": "#a0aec0", "user": "#718096", "background": "#f2f2f2"}

# ----------------------------------------------------------------------------------- schematics
# Boxes are sized from the measured extent of their text (data units = inches), so type and frames always match.
TITLE_PT, BODY_PT, EDGE_PT = 8.5, 7.0, 6.6
PAD = 0.07


def canvas(w, h):
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis("off")
    return fig, ax


def measure(ax, text, size, weight="normal"):
    t = ax.text(0, 0, text, fontsize=size, weight=weight, linespacing=1.3)
    r = ax.figure.canvas.get_renderer()
    bb = t.get_window_extent(r); t.remove()
    dpi = ax.figure.dpi
    return bb.width / dpi, bb.height / dpi


def box_size(ax, title, body, ts=None, bs=None):
    ts, bs = ts or TITLE_PT, bs or BODY_PT
    tw, th = measure(ax, title, ts, "bold")
    bw, bh = measure(ax, body, bs) if body else (0, 0)
    return max(tw, bw) + 2 * PAD, th + (bh + 0.06 if body else 0) + 2 * PAD


def draw_box(ax, cx, cy, w, h, title, body="", fc="#edf2f7", ec="#4a5568", ts=None, bs=None):
    ts, bs = ts or TITLE_PT, bs or BODY_PT
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h, boxstyle="round,pad=0,rounding_size=0.06", fc=fc, ec=ec, lw=0.9))
    _, th = measure(ax, title, ts, "bold")
    top = cy + h / 2 - PAD
    ax.text(cx, top, title, ha="center", va="top", fontsize=ts, weight="bold", linespacing=1.3)
    if body:
        ax.text(cx, top - th - 0.06, body, ha="center", va="top", fontsize=bs, linespacing=1.3, color="#2d3748")
    return (cx, cy, w, h)


def edge_point(b, toward):
    """Point on the border of box b=(cx,cy,w,h) in the direction of `toward`."""
    cx, cy, w, h = b; dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    s = min((w / 2) / abs(dx) if dx else np.inf, (h / 2) / abs(dy) if dy else np.inf)
    return cx + dx * s, cy + dy * s


def connect(ax, a, b, text="", style="-|>", color="#2d3748", rad=0.0, label_at=0.5, above=False):
    p, q = edge_point(a, b[:2]), edge_point(b, a[:2])
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=9, color=color, lw=0.9, shrinkA=2, shrinkB=2,
                                 connectionstyle=f"arc3,rad={rad}"))
    if text:
        mx, my = p[0] + (q[0] - p[0]) * label_at, p[1] + (q[1] - p[1]) * label_at
        if above:
            ax.text(mx, my + 0.05, text, ha="center", va="bottom", fontsize=EDGE_PT, color=color, style="italic")
        else:
            ax.text(mx, my, text, ha="center", va="center", fontsize=EDGE_PT, color=color, style="italic",
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"))


def fig_pipeline():
    stages = [
        ("Inputs", "EnergyPlus model\nweather file\nzone name\ncase configuration", "#fefcbf"),
        ("1  Adapt &\nsimulate", "inject outputs,\nrun periods, faults\n\u2192 EnergyPlus\n(cached)", "#e6fffa"),
        ("2  Read &\ntrace", "geometry (world\ncoordinates), plenum,\nroof; HVAC graph\n\u2192 serving chain", "#ebf8ff"),
        ("3  Generate", "air devices\nplenum distribution\nrooftop / plant\n(sizing + layout rules)", "#faf5ff"),
        ("4  Thermal\nstate", "surfaces: simulated\nelements: model\nregistry; spaces:\nenvironment series", "#fff5f5"),
        ("5  Output &\nimage", "OpenUSD + RDF\n+ provenance;\nreference / Isaac\nthermal cameras", "#f0fff4")]
    ts, bs, W, M = 7.6, 6.4, 7.4, 0.06
    fig, ax = canvas(W, 3.0)
    sizes = [box_size(ax, t, b, ts, bs) for t, b, _ in stages]
    bw, bh = max(s[0] for s in sizes) + 0.04, max(s[1] for s in sizes)
    gap = (W - 2 * M - len(stages) * bw) / (len(stages) - 1)
    assert gap > 0.12, f"stage boxes too wide ({bw:.2f} in)"
    ot = "Ontology & provenance (shared by every stage)"
    ob = ("Space \u00b7 Surface \u00b7 Opening \u00b7 SimComponent \u00b7 Element \u00b7 Port \u00b7 Stream \u00b7 Camera \u00b7 Fault\n"
          "levels: simulated \u00b7 physics \u00b7 engineering rule \u00b7 heuristic \u00b7 assumed layout \u00b7 user")
    ow, oh = box_size(ax, ot, ob, ts, bs)
    H = M + bh + 0.42 + oh + M
    plt.close(fig); fig, ax = canvas(W, H)
    cy = H - M - bh / 2
    boxes = [draw_box(ax, M + bw / 2 + k * (bw + gap), cy, bw, bh, t, b, fc=c, ts=ts, bs=bs) for k, (t, b, c) in enumerate(stages)]
    for a, b in zip(boxes[:-1], boxes[1:]):
        connect(ax, a, b)
    x0, x1 = boxes[1][0] - bw / 2, boxes[-1][0] + bw / 2
    ont = draw_box(ax, (x0 + x1) / 2, M + oh / 2, x1 - x0, oh, ot, ob, fc="#f7fafc", ts=ts, bs=bs)
    for b in boxes[1:]:
        ax.add_patch(FancyArrowPatch((b[0], b[1] - b[3] / 2), (b[0], ont[1] + ont[3] / 2), arrowstyle="<|-|>",
                                     mutation_scale=8, color="#a0aec0", lw=0.9, shrinkA=2, shrinkB=2))
    fig.savefig(OUT / "fig1_pipeline.png", dpi=300); plt.close(fig)


def fig_ontology():
    nodes = {"Space": "Zone \u00b7 Plenum \u00b7 Outdoors\nair, MRT, reflected,\nbackground temperature",
             "Surface": "EnergyPlus surface\ninside / outside face\nT_s simulated",
             "Opening": "window \u00b7 door \u00b7\ninjected defect",
             "Element": "duct \u00b7 pipe \u00b7 valve \u00b7\ndiffuser \u00b7 casing \u00b7\ncondenser \u00b7 motor",
             "SimComponent": "EnergyPlus HVAC object\n(data only) with Ports\nbound to nodes",
             "Camera": "intrinsics, pose,\n8\u201314 \u00b5m, camera settings",
             "Stream": "node temperature\n& mass flow series",
             "Fault": "envelope defect \u00b7\nduct insulation gap \u00b7\nFaultModel"}
    W, H = 7.4, 4.1
    fig, ax = canvas(W, H)
    sizes = {k: box_size(ax, k, v) for k, v in nodes.items()}
    bw, bh = max(s[0] for s in sizes.values()), max(s[1] for s in sizes.values())
    cols = [0.15 + bw / 2, 0.15 + bw / 2 + (W - 0.3 - bw) / 3, 0.15 + bw / 2 + 2 * (W - 0.3 - bw) / 3, W - 0.15 - bw / 2]
    rows = [H - 0.12 - bh / 2, H / 2, 0.12 + bh / 2]
    at = {"Surface": (1, 0), "SimComponent": (2, 0), "Stream": (3, 0), "Space": (0, 1), "Opening": (1, 1), "Element": (2, 1),
          "Camera": (0, 2), "Fault": (1, 2)}
    fc = {"SimComponent": "#ebf8ff", "Stream": "#ebf8ff", "Element": "#fffaf0", "Fault": "#fffaf0"}
    B = {k: draw_box(ax, cols[c], rows[r], bw, bh, k, nodes[k], fc=fc.get(k, "#f7fafc")) for k, (c, r) in at.items()}
    connect(ax, B["Surface"], B["Space"], "locatedIn")
    e, sp_ = B["Element"], B["Space"]                       # routed under the Opening box as a polyline
    yb = e[1] - e[3] / 2; yl = yb - 0.24
    x0, x1 = e[0] - e[2] * 0.3, sp_[0] + sp_[2] * 0.3
    ax.plot([x0, x0, x1], [yb - 0.02, yl, yl], color="#2d3748", lw=0.9, solid_capstyle="butt")
    ax.add_patch(FancyArrowPatch((x1, yl), (x1, yb), arrowstyle="-|>", mutation_scale=9, color="#2d3748", lw=0.9, shrinkA=0, shrinkB=2))
    ax.text((x1 + B["Opening"][0]) / 2, yl, "locatedIn", ha="center", va="center", fontsize=EDGE_PT, color="#2d3748",
            style="italic", bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"))
    connect(ax, B["Surface"], B["Opening"], "hosts")
    connect(ax, B["Element"], B["SimComponent"], "represents")
    connect(ax, B["SimComponent"], B["Stream"], "hasPort\n\u2192 node", above=True)
    connect(ax, B["Element"], B["Stream"], "streamNode")
    connect(ax, B["Camera"], B["Space"], "observes")
    connect(ax, B["Fault"], B["Opening"], "affects")
    connect(ax, B["Fault"], B["Element"], "affects")
    ax.text(cols[3], rows[1], "connectedTo\n(Element \u2192 Element)\n\nfeeds\n(SimComponent \u2192 SimComponent)",
            ha="center", va="center", fontsize=EDGE_PT + 0.4, style="italic", color="#4a5568", linespacing=1.35)
    fig.savefig(OUT / "fig2_ontology.png", dpi=300); plt.close(fig)


# ----------------------------------------------------------------------------------- topology
ROLE_COLORS = {"terminal": "#c53b7f", "air_supply": "#2f73b8", "air_distribution": "#4f9bd6", "plant_supply": "#2f855a",
               "plant_demand": "#68b88a", "zone_equipment": "#c53b7f", "other": "#a0aec0"}


def fig_topology():
    """Traced serving chain of case B as lanes: air path to the zone, and one lane per plant loop feeding its coil."""
    st = Usd.Stage.Open(str(CS / "B_hvac_system/output/B_hvac_system.usda"))
    G = nx.DiGraph(); info = {}
    for p in st.GetPrimAtPath("/World/Systems").GetChildren():
        k = str(p.GetPath()); ports = list(p.GetAttribute("ts:ports").Get())
        info[k] = dict(name=p.GetAttribute("ts:simName").Get(), typ=p.GetAttribute("ts:simType").Get(),
                       role=p.GetAttribute("ts:role").Get(), system=p.GetAttribute("ts:system").Get(),
                       air=any("|air|" in x for x in ports))
        G.add_node(k)
    for p in st.GetPrimAtPath("/World/Systems").GetChildren():
        for t in p.GetRelationship("ts:feeds").GetTargets():
            G.add_edge(str(p.GetPath()), str(t))
    for k in [k for k in G if info[k]["typ"].startswith("Pipe:")]:
        for a in G.predecessors(k):
            for b in G.successors(k):
                if a != b:
                    G.add_edge(a, b)
        G.remove_node(k)
    air = [k for k in G if info[k]["air"] and info[k]["role"] != "plant_supply"]
    order = list(nx.topological_sort(G.subgraph(air)))
    lanes = {}
    for k in G:
        if k not in air:
            lanes.setdefault(info[k]["system"], []).append(k)
    short = lambda n: n.replace("Central AHU ", "").replace("Chilled Water Loop ", "").replace("Hot Water Loop ", "").replace("Office ", "")
    label = lambda k: textwrap.fill(short(info[k]["name"]), 10)
    sub = lambda k: info[k]["typ"].split(":")[-1]
    W, H = 7.4, 3.5
    fig, ax = canvas(W, H)
    n = len(order) + 1
    left = 0.78
    for ts in (7.2, 6.8, 6.4, 6.0, 5.6):                         # largest type that leaves a visible gap between boxes
        ss = ts - 1.4
        bw = max(max(measure(ax, label(k), ts, "bold")[0], measure(ax, sub(k), ss)[0]) for k in G) + 2 * PAD
        bw = max(bw, measure(ax, "Office\n(zone)", ts, "bold")[0] + 2 * PAD)
        step = (W - left - 0.06 - bw) / (n - 1)
        if step - bw >= 0.2:
            break
    bh = max(measure(ax, label(k), ts, "bold")[1] for k in G) + measure(ax, "X", ss)[1] + 2 * PAD + 0.08
    xs = {k: left + bw / 2 + i * step for i, k in enumerate(order)}
    y_air = H - 0.55 - bh / 2
    pos = {k: (xs[k], y_air) for k in order}
    lane_y, y = {}, y_air - bh - 0.35
    for loop in sorted(lanes):
        ks = list(nx.topological_sort(G.subgraph(lanes[loop])))
        tgt = next((b for k in ks for b in G.successors(k) if b in xs), None)
        xi = order.index(tgt) - len(ks) if tgt else 0
        for i, k in enumerate(ks):
            pos[k] = (left + bw / 2 + (xi + i) * step, y)
        lane_y[loop] = y; y -= bh + 0.22

    def node_box(k, x, yy):
        c = ROLE_COLORS.get(info[k]["role"], "#a0aec0")
        ax.add_patch(FancyBboxPatch((x - bw / 2, yy - bh / 2), bw, bh, boxstyle="round,pad=0,rounding_size=0.05", fc=c, ec="#1a202c", lw=0.6))
        ax.text(x, yy + bh / 2 - PAD, label(k), ha="center", va="top", fontsize=ts, weight="bold", color="white", linespacing=1.3)
        ax.text(x, yy - bh / 2 + PAD, sub(k), ha="center", va="bottom", fontsize=ss, color="white")
        return (x, yy, bw, bh)
    B = {k: node_box(k, *pos[k]) for k in pos}
    zx = left + bw / 2 + (n - 1) * step
    ax.add_patch(FancyBboxPatch((zx - bw / 2, y_air - bh / 2), bw, bh, boxstyle="round,pad=0,rounding_size=0.05", fc="#fefcbf", ec="#1a202c", lw=0.9))
    ax.text(zx, y_air, "Office\n(zone)", ha="center", va="center", fontsize=ts, weight="bold", linespacing=1.3)
    Z = (zx, y_air, bw, bh)
    lane_of = {k: info[k]["system"] for k in pos if k not in xs}
    for a, b in G.edges:
        if a in xs and b in xs:
            connect(ax, B[a], B[b])
        elif a in lane_of and b in lane_of and lane_of[a] == lane_of[b]:
            connect(ax, B[a], B[b])
        elif a in lane_of and b in xs and a == max((k for k in lane_of if lane_of[k] == lane_of[a]), key=lambda k: pos[k][0]):
            connect(ax, B[a], B[b], rad=-0.12)
    connect(ax, B[order[-1]], Z)
    ax.add_patch(FancyArrowPatch((zx, y_air + bh / 2), (xs[order[0]], y_air + bh / 2), arrowstyle="-|>", mutation_scale=9,
                                 color="#a0aec0", lw=0.9, ls="--", connectionstyle="arc3,rad=0.18", shrinkA=2, shrinkB=2))
    ax.text((zx + xs[order[0]]) / 2, H - 0.12, "return air (zone \u2192 return plenum)", ha="center", va="top", fontsize=EDGE_PT, color="#718096", style="italic")
    ax.text(left - 0.08, y_air, "air", ha="right", va="center", fontsize=ts, color="#2f73b8", weight="bold")
    for loop, yy in lane_y.items():
        ax.text(left - 0.08, yy, " ".join(loop.split(" ")[:2]).replace(" ", "\n") + "\nloop", ha="right", va="center",
                fontsize=ts, color="#2f855a", weight="bold", linespacing=1.2)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (ROLE_COLORS["terminal"], ROLE_COLORS["air_supply"], ROLE_COLORS["air_distribution"],
                                                              ROLE_COLORS["plant_supply"], ROLE_COLORS["plant_demand"])]
    y_low = min(lane_y.values()) - bh / 2 - 0.12
    ax.legend(handles, ["terminal", "air supply", "air distribution", "plant supply", "plant demand"], loc="upper right",
              ncol=5, fontsize=EDGE_PT + 0.4, frameon=False, handlelength=1.0, columnspacing=1.0,
              bbox_to_anchor=(W - 0.02, y_low), bbox_transform=ax.transData)
    ymin = y_low - 0.3                                            # crop the canvas to the content, keeping 1 unit = 1 inch
    fig.set_size_inches(W, H - ymin); ax.set_ylim(ymin, H)
    fig.savefig(OUT / "fig3_topology_caseB.png", dpi=300); plt.close(fig)


# ----------------------------------------------------------------------------------- geometry renders
CLASS_COLORS = {"Wall": "#d9d4c7", "Floor": "#b7b09c", "Ceiling": "#ece9e1", "Roof": "#9c9a92", "Window": "#9ecae1",
                "Door": "#c49a6c", "Defect": "#e53e3e", "Duct": "#90a4ae", "Pipe": "#ed8936", "Valve": "#d69e2e",
                "TerminalBox": "#805ad5", "CoilSection": "#b794f4", "Diffuser": "#f7fafc", "ReturnGrille": "#cbd5e0",
                "EquipmentCasing": "#68a37d", "CondenserCoil": "#4a7a5c", "CondenserFan": "#2d3748", "Motor": "#2b6cb0",
                "Louver": "#a0aec0", "RoofSurface": "#9c9a92", "InsulationDefect": "#e53e3e"}


def prim_color(p):
    cls = p.GetAttribute("semantic:class").Get()
    if cls in ("Surface", "Opening"):
        cls = p.GetAttribute("ts:surfaceType").Get()
    if cls == "Pipe" and p.GetAttribute("mep:streamNode").Get() and "CHW" in str(p.GetPath()).upper() + str(p.GetAttribute("mep:streamNode").Get()).upper():
        return "#3182ce"
    return CLASS_COLORS.get(cls, "#cccccc")


def geometry_render(usd, eye, target, hfov, res, hide=(), ax=None, title=""):
    st = Usd.Stage.Open(str(usd))
    prims = [p for p in thermal_prims(st) if not any(h in str(p.GetPath()) for h in hide)]
    meshes = accel([usd_faces(p) for p in prims])
    o, d = rays_from(eye, target, hfov, res)
    hit, t, nrm = raycast(o, d.reshape(-1, 3), meshes, normals=True)
    light = np.array([0.35, -0.5, 0.8]); light /= np.linalg.norm(light)
    view = -d.reshape(-1, 3) / np.linalg.norm(d.reshape(-1, 3), axis=1, keepdims=True)
    nn = np.where((nrm * view).sum(1, keepdims=True) < 0, -nrm, nrm)
    shade = 0.45 + 0.55 * np.clip((nn * light).sum(1), 0, 1)
    cols = np.array([matplotlib.colors.to_rgb(prim_color(p)) for p in prims] + [(1, 1, 1)])
    img = cols[np.where(hit >= 0, hit, len(prims))] * np.where(hit >= 0, shade, 1.0)[:, None]
    ax.imshow(np.clip(img.reshape(res[1], res[0], 3), 0, 1)); ax.axis("off"); ax.set_title(title, fontsize=8.5)


def roof_view(usd, dist=1.0):
    """Eye/target framing the rooftop equipment from its bounding box (south-east, elevated)."""
    st = Usd.Stage.Open(str(usd))
    pts = np.vstack([np.vstack(usd_faces(p)) for p in Usd.PrimRange(st.GetPrimAtPath("/World/Outdoors/Elements"))
                     if p.HasAttribute("thermal:surfaceTemperature") and p.GetAttribute("semantic:class").Get() != "RoofSurface"])
    lo, hi = pts.min(0), pts.max(0); c = (lo + hi) / 2; r = np.linalg.norm(hi - lo)
    return tuple(c + dist * r * np.array([0.55, -0.85, 0.55])), tuple(c - [0, 0, 0.2 * (hi[2] - lo[2])])


def fig_geometry():
    B = CS / "B_hvac_system/output/B_hvac_system.usda"
    C = CS / "C_doe_medium_office/output/C_doe_medium_office.usda"
    fig, axs = plt.subplots(2, 2, figsize=(7.2, 5.2))
    hideB = ("/Plenum/Surfaces/Plenum_Slab", "/Plenum/Surfaces/Plenum_South", "/Plenum/Surfaces/Plenum_West",
             "/Office/Surfaces/South", "/Office/Surfaces/West", "/Outdoors/", "Insulation_Void")
    geometry_render(B, (-2.2, -3.0, 6.2), (2.6, 2.2, 2.6), 55, (640, 440), hideB, axs[0, 0], "(a) Case B: office + plenum (cut-away)")
    eB, tB = roof_view(B, 0.95)
    geometry_render(B, eB, tB, 50, (640, 440), ("/Office/", "/Plenum/"), axs[0, 1],
                    "(b) Case B: rooftop AHU, chiller, pump, piping")
    hideC = ("Plenum/Surfaces/TopFloor_Plenum_Roof", "Building_Roof", "/Outdoors/", "Plenum_Wall_South", "ZN_1_Wall_South")
    geometry_render(C, (14.0, -9.0, 17.5), (24.0, 3.0, 12.5), 55, (640, 440), hideC, axs[1, 0], "(c) Case C: perimeter zone + top-floor plenum")
    eC, tC = roof_view(C, 1.0)
    geometry_render(C, eC, tC, 50, (640, 440), ("Perimeter_top_ZN_1/", "TopFloor_Plenum/"), axs[1, 1],
                    "(d) Case C: packaged rooftop unit on the model's roof")
    fig.tight_layout(); fig.savefig(OUT / "fig4_geometry.png", dpi=200); plt.close(fig)


# ----------------------------------------------------------------------------------- thermal results
def load_view(case, cam):
    d = CS / case / "output" / "renders" / cam
    return d, np.load(d / "segmentation.npy"), np.load(d / "provenance_map.npy")


def prov_rgb(pm):
    cols = np.array([matplotlib.colors.to_rgb(PROV_COLORS[k]) for k in LEVELS] + [matplotlib.colors.to_rgb(PROV_COLORS["background"])])
    return cols[pm]


def fig_thermal(case, rows, name):
    fig, axs = plt.subplots(len(rows), 3, figsize=(7.2, 1.95 * len(rows)))
    for r, (cam, f1, f2) in enumerate(rows):
        d, seg, pm = load_view(case, cam)
        axs[r, 0].imshow(prov_rgb(pm)); axs[r, 0].set_title(f"{cam}: pixel provenance", fontsize=7.5)
        for c, f in ((1, f1), (2, f2)):
            a = np.load(d / f"{f.replace(' ', '_').replace(':', '')}_apparent.npy")
            im = axs[r, c].imshow(a, cmap="inferno", vmin=np.percentile(a, 1), vmax=np.percentile(a, 99.5))
            cb = fig.colorbar(im, ax=axs[r, c], fraction=0.04, pad=0.02); cb.ax.tick_params(labelsize=6)
            axs[r, c].set_title(f"{f}  (apparent °C)", fontsize=7.5)
        for c in range(3):
            axs[r, c].axis("off")
    handles = [plt.Rectangle((0, 0), 1, 1, color=PROV_COLORS[k]) for k in ("simulated", "physics", "heuristic", "background")]
    fig.legend(handles, ["simulated", "physics", "heuristic", "background"], loc="lower center", ncol=4, fontsize=7, frameon=False)
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / name, dpi=200); plt.close(fig)


def fig_timeseries():
    st = Usd.Stage.Open(str(CS / "B_hvac_system/output/B_hvac_system.usda")); ser = Series(st); labs = labels(st)
    win = [i for i, l in enumerate(labs) if l.startswith("WINTER 01-15")]; summ = [i for i, l in enumerate(labs) if l.startswith("SUMMER 07-16")]
    h = np.arange(1, 97) * 0.25
    g = lambda path, attr, tcs: [ser.get(st.GetPrimAtPath(path), attr, t) for t in tcs]
    T = "thermal:surfaceTemperature"
    fig, axs = plt.subplots(2, 2, figsize=(7.2, 4.9), sharex=True)
    a = axs[0, 0]
    a.plot(h, g("/World/Office/Surfaces/TS_DEFECT_Insulation_Void", T, win), c="#e53e3e", lw=1.6, label="defect (EnergyPlus)")
    a.plot(h, g("/World/Office/Surfaces/South_Wall", T, win), c="#2b6cb0", lw=1.6, label="insulated wall")
    a.plot(h, g("/World/Office", "thermal:airTemperature", win), c="k", ls="--", lw=1, label="zone air")
    a.plot(h, g("/World/Outdoors", "thermal:airTemperature", win), c="gray", ls=":", lw=1, label="outdoor air")
    a.set_title("(a) Envelope, winter day"); a.set_ylabel("°C"); a.legend(fontsize=6.5, frameon=False)
    a = axs[0, 1]; a2 = a.twinx()
    a.plot(h, g("/World/Office/Elements/Supply_Diffuser_1", T, win), c="#319795", lw=1.6, label="diffuser face")
    a.plot(h, g("/World/Office/Surfaces/Ceiling", T, win), c="#dd6b20", lw=1.2, label="ceiling")
    a.plot(h, g("/World/Plenum/Elements/Discharge_Duct_1", "mep:fluidTemperature", win),
           c="#3182ce", lw=1, ls="--", label="supply air (node)")
    a2.fill_between(h, [ser.get(st.GetPrimAtPath("/World/Plenum/Elements/Discharge_Duct_1"), "mep:fluidMassFlow", t) * 1000 for t in win],
                    color="gray", alpha=0.18, step="mid"); a2.set_ylabel("room supply flow (g/s)", fontsize=7)
    a.set_title("(b) Terminal and diffuser, winter day"); a.legend(fontsize=6.5, frameon=False, loc="upper right")
    a = axs[1, 0]
    a.plot(h, g("/World/Plenum/Elements/HW_Control_Valve", T, win), c="#c53030", lw=1.6, label="reheat valve (bare)")
    a.plot(h, g("/World/Plenum/Elements/HW_Supply_Pipe", T, win), c="#dd6b20", lw=1.6, label="HW pipe jacket")
    a.plot(h, g("/World/Plenum/Elements/HW_Supply_Pipe", "mep:fluidTemperature", win), c="#dd6b20", ls=":", lw=1, label="water inside")
    a.plot(h, g("/World/Plenum/Elements/Duct_Insulation_Gap", T, win), c="#2b6cb0", lw=1.6, label="duct, insulation gap")
    a.plot(h, g("/World/Plenum/Elements/Discharge_Duct_1", T, win), c="#63b3ed", lw=1.2, label="duct, wrapped")
    a.set_title("(c) Plenum, winter day"); a.set_ylabel("°C"); a.set_xlabel("hour"); a.legend(fontsize=6.2, frameon=False, ncol=2)
    a = axs[1, 1]
    roof = [p for p in Usd.PrimRange(st.GetPrimAtPath("/World/Outdoors")) if p.HasAttribute(T)]
    pick = lambda cls: next(p for p in roof if p.GetAttribute("semantic:class").Get() == cls)
    for cls, lab, c in (("RoofSurface", "roof membrane", "#4a5568"), ("CondenserFan", "condenser fan", "#c53030"),
                        ("Motor", "pump motor", "#805ad5"), ("Valve", "CHW valve (bare)", "#319795")):
        a.plot(h, [ser.get(pick(cls), T, t) for t in summ], c=c, lw=1.5, label=lab)
    chw = next(p for p in roof if p.GetAttribute("semantic:class").Get() == "Pipe")
    a.plot(h, [ser.get(chw, T, t) for t in summ], c="#3182ce", lw=1.2, label="CHW pipe jacket")
    a.plot(h, g("/World/Outdoors", "thermal:airTemperature", summ), c="k", ls="--", lw=1, label="outdoor air")
    a.set_title("(d) Roof, summer day"); a.set_xlabel("hour"); a.legend(fontsize=6.2, frameon=False, ncol=2)
    for a in axs.ravel():
        a.set_xlim(0, 24); a.set_xticks(range(0, 25, 6)); a.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT / "fig6_timeseries_caseB.png", dpi=200); plt.close(fig)


def fig_provenance_shares():
    rows = []
    for case, tag in (("B_hvac_system", "B"), ("C_doe_medium_office", "C")):
        for f in sorted((CS / case / "output/renders").glob("*/provenance_share.json")):
            rows.append((f"{tag}: {f.parent.name.replace('Camera', '')}", json.loads(f.read_text())))
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    left = np.zeros(len(rows))
    for k in ("simulated", "physics", "engineering_rule", "heuristic", "assumed_layout", "background"):
        v = np.array([r[1].get(k, 0) * 100 for r in rows])
        if v.any():
            ax.barh(range(len(rows)), v, left=left, color=PROV_COLORS[k], label=k.replace("_", " "), edgecolor="white", lw=0.5)
            for i, (x, w) in enumerate(zip(left, v)):
                if w > 7:
                    ax.text(x + w / 2, i, f"{w:.0f}", ha="center", va="center", fontsize=6.5, color="white" if k != "background" else "#4a5568")
            left += v
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in rows], fontsize=7.5); ax.invert_yaxis()
    ax.set_xlabel("share of image pixels (%)"); ax.set_xlim(0, 100)
    ax.legend(ncol=5, fontsize=7, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout(); fig.savefig(OUT / "fig8_pixel_provenance.png", dpi=200); plt.close(fig)


def fig_thermal_cases():
    fig_thermal("B_hvac_system", [("WallCamera", "WINTER 01-15 07:00", "SUMMER 07-16 15:00"),
                                  ("CeilingCamera", "WINTER 01-15 07:30", "SUMMER 07-16 15:00"),
                                  ("PlenumCamera", "WINTER 01-15 07:30", "SUMMER 07-16 15:00"),
                                  ("RooftopCamera", "WINTER 01-15 03:00", "SUMMER 07-16 15:00")], "fig5_thermal_caseB.png")
    fig_thermal("C_doe_medium_office", [("WindowCamera", "WINTER 01-15 07:00", "SUMMER 07-16 14:00"),
                                        ("DiffuserCamera", "WINTER 01-15 07:30", "SUMMER 07-16 15:00"),
                                        ("PlenumCamera", "WINTER 01-15 07:30", "SUMMER 07-16 15:00"),
                                        ("RooftopCamera", "WINTER 01-15 03:00", "SUMMER 07-16 15:00")], "fig7_thermal_caseC.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    for f in (fig_pipeline, fig_ontology, fig_topology, fig_geometry, fig_timeseries, fig_provenance_shares, fig_thermal_cases):
        if not only or f.__name__ in only:
            f()
    print("figures in", OUT)
