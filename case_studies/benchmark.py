"""Benchmarks of the physics-level element models and of the one-dimensional envelope defect.

1. Element models. The pipeline uses fixed film coefficients (defaults.yaml). Each plenum element model is compared
   with an independent steady calculation that uses standard correlations instead: natural convection from a
   horizontal cylinder (Churchill and Chu, 1975), linearised long-wave radiation to surroundings at air temperature,
   radial conduction through the insulation, and, for the bare valve, a water-side film from laminar (Nu = 3.66) and
   turbulent (Dittus-Boelter, 1 m/s) internal flow. Surface temperatures are compared over the operating range of the
   case B plenum.
2. Envelope defect. EnergyPlus treats the void as a separate one-dimensional surface. A steady two-dimensional
   finite-volume model of a section through case B's wall (stucco / insulation / gypsum, with the void's insulation
   replaced by the same 0.17 m2K/W cavity) shows how lateral conduction changes the inside-surface contrast and how far
   it smears the void's edge.
Writes case_studies/benchmark.json and docs/benchmark.md.  Run:  python case_studies/benchmark.py
"""
import json
from pathlib import Path

import numpy as np
import yaml
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

ROOT = Path(__file__).resolve().parents[1]
SIGMA = 5.670374419e-8
D = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))
TH, MAT = D["thermal"], D["materials"]
H_I, H_O, K_INS = TH["film_coefficients"]["inside_air"], TH["film_coefficients"]["plenum"], TH["insulation"]["conductivity"]


# ---------------------------------------------------------------- reference correlations
def air(t_film_c):
    t = t_film_c + 273.15
    return 0.0241 * (t / 273.15) ** 0.81, 1.33e-5 * (t / 273.15) ** 1.75, 0.71     # k, nu, Pr


def h_outer(ts, ta, d, eps):
    """Natural convection from a horizontal cylinder (Churchill and Chu) + linearised radiation (W/m2K)."""
    k, nu, pr = air((ts + ta) / 2)
    ra = 9.81 / ((ts + ta) / 2 + 273.15) * abs(ts - ta) * d ** 3 / nu ** 2 * pr
    nu_d = (0.60 + 0.387 * ra ** (1 / 6) / (1 + (0.559 / pr) ** (9 / 16)) ** (8 / 27)) ** 2
    t1, t2 = ts + 273.15, ta + 273.15
    return nu_d * k / d + eps * SIGMA * (t1 + t2) * (t1 ** 2 + t2 ** 2)


def solve_surface(tf, ta, r_in, d_out, eps):
    """Surface temperature with resistance r_in (m2K/W, per outer area) between fluid and surface."""
    ts = ta + 0.5 * (tf - ta)
    for _ in range(200):
        h = h_outer(ts, ta, d_out, eps)
        new = (tf / r_in + h * ta) / (1 / r_in + h)
        if abs(new - ts) < 1e-6:
            break
        ts = 0.5 * (ts + new)
    return ts


def element_benchmarks():
    ta = 24.0
    out = {}
    # insulated hot-water pipe (DN15, 21 mm OD, 25 mm jacket, all-service jacket)
    r1 = 0.0213 / 2; t_ins = TH["insulation"]["pipe_plenum_m"]; r2 = r1 + t_ins; eps = MAT["asj_jacket"]["emissivity"]
    rows = []
    for tf in (30.0, 45.0, 65.0, 80.0):
        r_cond = r2 * np.log(r2 / r1) / K_INS
        model = ta - (ta - tf) * (1 / H_O) / (r_cond + 1 / H_O)
        ref = solve_surface(tf, ta, r_cond, 2 * r2, eps)
        rows.append((tf, model, ref))
    out["insulated_pipe: hot-water pipe, 21 mm + 25 mm, ε 0.90"] = rows
    # wrapped round duct (200 mm, foil-faced wrap)
    r1 = 0.10; t_w = TH["insulation"]["duct_wrap_m"]; r2 = r1 + t_w; eps = MAT["fsk_foil"]["emissivity"]
    rows = []
    for tf in (12.0, 16.0, 30.0, 35.0):
        model = ta - (ta - tf) * (1 / H_O) / (1 / H_I + t_w / K_INS + 1 / H_O)
        ref = solve_surface(tf, ta, r2 / (r1 * H_I) + r2 * np.log(r2 / r1) / K_INS, 2 * r2, eps)
        rows.append((tf, model, ref))
    out["insulated_flat: 200 mm duct, 38 mm foil-faced wrap, ε 0.30"] = rows
    # bare galvanised duct (the insulation gap)
    eps = MAT["galv_bare"]["emissivity"]
    rows = []
    for tf in (12.0, 16.0, 30.0, 35.0):
        model = tf + (ta - tf) * (1 / H_I) / (1 / H_I + 1 / H_O)
        ref = solve_surface(tf, ta, 1 / H_I, 0.20, eps)
        rows.append((tf, model, ref))
    out["bare_duct: 200 mm galvanised duct, ε 0.25"] = rows
    # bare valve / copper bends on a DN15 hot-water line (fixed 3 % coupling in the pipeline)
    eps = MAT["brass_valve"]["emissivity"]; d_i, d_o = 0.0158, 0.0213
    k_w, nu_w, pr_w = 0.65, 0.45e-6, 2.9                       # water near 60 °C
    h_lam = 3.66 * k_w / d_i
    re = 1.0 * d_i / nu_w
    h_turb = 0.023 * re ** 0.8 * pr_w ** 0.3 * k_w / d_i
    rows = []
    for tf in (30.0, 45.0, 65.0, 80.0):
        model = tf + (ta - tf) * TH["bare_pipe_coupling"]
        ref_lam = solve_surface(tf, ta, d_o / (d_i * h_lam), d_o, eps)
        ref_turb = solve_surface(tf, ta, d_o / (d_i * h_turb), d_o, eps)
        rows.append((tf, model, ref_lam, ref_turb))
    out["bare_pipe: DN15 valve body, ε 0.60 (reference: laminar / turbulent water side)"] = rows
    return out


# ---------------------------------------------------------------- stagnation time constants (lumped capacitance)
def stagnation_benchmarks():
    """tau = C / UA per metre of run: heat capacity of the trapped fluid, the wall and half the insulation over the
    conductance from fluid to plenum (inner film, wall-free insulation, outer convection + radiation from the
    reference correlations at the initial temperature difference)."""
    ta = 24.0
    tau_def = TH["stagnation_tau_min"]
    rho_cp = {"air": 1.2 * 1005, "water": 985 * 4183, "steel": 7850 * 470, "glass_wool": 24 * 840}
    cases = []
    # 200 mm round supply duct, 0.6 mm galvanised steel, foil-faced wrap; cold supply air trapped at 14 °C
    r1 = 0.10; t_w = TH["insulation"]["duct_wrap_m"]; r2 = r1 + t_w
    c = rho_cp["air"] * np.pi * r1 ** 2 + rho_cp["steel"] * 2 * np.pi * r1 * 0.0006 \
        + 0.5 * rho_cp["glass_wool"] * np.pi * (r2 ** 2 - r1 ** 2)
    r_in = 1 / (H_I * 2 * np.pi * r1) + np.log(r2 / r1) / (2 * np.pi * K_INS)
    cases.append(("wrapped 200 mm supply duct (air)", "air", 14.0, c, r_in, 2 * r2, MAT["fsk_foil"]["emissivity"]))
    # DN15 steel hot-water pipe (21.3 x 2.77 mm), 25 mm insulation with all-service jacket; water trapped at 60 °C
    ro, ri = 0.0213 / 2, 0.0158 / 2; t_ins = TH["insulation"]["pipe_plenum_m"]; r2 = ro + t_ins
    c_pipe = rho_cp["water"] * np.pi * ri ** 2 + rho_cp["steel"] * np.pi * (ro ** 2 - ri ** 2)
    c = c_pipe + 0.5 * rho_cp["glass_wool"] * np.pi * (r2 ** 2 - ro ** 2)
    r_in = np.log(r2 / ro) / (2 * np.pi * K_INS)                     # water film and steel wall negligible
    cases.append(("insulated DN15 hot-water pipe (water)", "water", 60.0, c, r_in, 2 * r2, MAT["asj_jacket"]["emissivity"]))
    # bare DN15 steel pipe as a proxy for the valve body and coil bends (a cast body adds mass and area)
    cases.append(("bare DN15 pipe, proxy for the valve body (water)", "valve", 60.0, c_pipe, 0.0, 0.0213,
                  MAT["brass_valve"]["emissivity"]))
    rows = []
    for name, kind, tf, c, r_in, d_out, eps in cases:
        r_in_area = max(r_in * np.pi * d_out, 1e-9)                 # per m2 of outer surface, for solve_surface
        ts = solve_surface(tf, ta, r_in_area, d_out, eps)
        h = h_outer(ts, ta, d_out, eps)
        ua = 1 / (r_in + 1 / (h * np.pi * d_out))                     # W/(m K)
        rows.append(dict(element=name, kind=kind, initial_fluid_C=tf, heat_capacity_J_per_mK=round(float(c), 1),
                         ua_W_per_mK=round(float(ua), 3), tau_lumped_min=round(float(c / ua / 60), 1),
                         tau_default_min=float(tau_def[kind])))
    return rows


# ---------------------------------------------------------------- 2-D section through the envelope defect
def defect_2d():
    layers = [("stucco", 0.025, 0.72), ("insulation", 0.090, 0.045), ("gypsum", 0.0127, 0.16)]     # outside -> inside
    r_cav = D["fault_defaults"]["envelope_defect"]["cavity_resistance"]
    h_in, h_out = 1 / 0.13, 1 / 0.04                     # ISO 6946 surface resistances (horizontal heat flow)
    t_in, t_out = 20.0, 0.0
    half_w, void_half, dx = 1.2, 0.4, 0.005
    nx = int(round(half_w / dx))
    xs = (np.arange(nx) + 0.5) * dx                     # distance from the void centre (symmetry at x = 0)
    zk, dzs = [], []
    for name, t, k in layers:
        n = max(4, int(round(t / 0.003)))
        dzs += [t / n] * n; zk += [(name, k)] * n
    nz = len(dzs)

    def kk(i, j):
        name, k = zk[j]
        return 0.090 / r_cav if (name == "insulation" and xs[i] < void_half) else k

    idx = lambda i, j: i * nz + j
    A = lil_matrix((nx * nz, nx * nz)); b = np.zeros(nx * nz)
    for i in range(nx):
        for j in range(nz):
            p, kc = idx(i, j), kk(i, j)
            diag = 0.0
            for ii in (i - 1, i + 1):                    # lateral neighbours (adiabatic at both ends)
                if 0 <= ii < nx:
                    g = dzs[j] / (dx / (2 * kc) + dx / (2 * kk(ii, j)))
                    A[p, idx(ii, j)] -= g; diag += g
            for jj in (j - 1, j + 1):                    # through-thickness neighbours
                if 0 <= jj < nz:
                    g = dx / (dzs[j] / (2 * kc) + dzs[jj] / (2 * kk(i, jj)))
                    A[p, idx(i, jj)] -= g; diag += g
            if j == 0:                                   # outside face
                g = dx / (1 / h_out + dzs[j] / (2 * kc)); diag += g; b[p] += g * t_out
            if j == nz - 1:                              # inside face
                g = dx / (1 / h_in + dzs[j] / (2 * kc)); diag += g; b[p] += g * t_in
            A[p, p] += diag
    T = spsolve(A.tocsr(), b).reshape(nx, nz)
    ts = T[:, -1] + (t_in - T[:, -1]) * 0 - 0                       # cell-centre temperature of the inside layer
    q_in = (t_in - T[:, -1]) / (1 / h_in + dzs[-1] / (2 * zk[-1][1]))
    ts = t_in - q_in / h_in                                        # inside surface temperature

    def one_d(void):
        r = 1 / h_out + 1 / h_in + sum((r_cav if (void and n == "insulation") else t / k) for n, t, k in layers)
        return t_in - (t_in - t_out) / r / h_in
    w1, v1 = one_d(False), one_d(True)
    c1, c2 = v1 - w1, ts[0] - ts[-1]
    frac = (ts - ts[-1]) / c2
    edge = lambda f: float(xs[np.argmax(frac < f)])
    return dict(boundary=f"steady, inside {t_in} °C (R_si 0.13), outside {t_out} °C (R_se 0.04)",
                one_d=dict(wall=round(w1, 2), void=round(v1, 2), contrast=round(c1, 2)),
                two_d=dict(void_centre=round(float(ts[0]), 2), far_wall=round(float(ts[-1]), 2), contrast=round(c2, 2)),
                centre_contrast_ratio_2d_to_1d=round(c2 / c1, 3),
                edge_90_to_10_percent_m=[round(edge(0.9), 3), round(edge(0.1), 3)],
                note="x measured from the void centre; the void edge is at 0.40 m")


def main():
    el = element_benchmarks()
    st = stagnation_benchmarks()
    d2 = defect_2d()
    worst = {}
    lines = ["# Benchmarks of the physics-level models", "",
             "Generated by `case_studies/benchmark.py`. Surroundings at 24 °C (plenum air = radiant temperature).", "",
             "## Element surface temperatures: pipeline model vs. standard correlations", "",
             "| Element | Fluid (°C) | Pipeline (°C) | Reference (°C) | Difference (K) |", "|---|---|---|---|---|"]
    for name, rows in el.items():
        for r in rows:
            refs = r[2:]
            diff = max((r[1] - x for x in refs), key=abs)
            worst[name] = max(worst.get(name, 0.0), abs(diff))
            lines.append(f"| {name} | {r[0]:.0f} | {r[1]:.2f} | {' / '.join(f'{x:.2f}' for x in refs)} | {diff:+.2f} |")
    lines += ["", "Largest absolute difference per model (K): " + "; ".join(f"{k.split(':')[0]} {v:.2f}" for k, v in worst.items()), "",
              "## Stagnation time constants: default vs. lumped capacitance", "",
              "tau = C / UA per metre: heat capacity of the trapped fluid, the steel wall and half the insulation, over the "
              "conductance to the plenum (outer convection and radiation from the correlations above).", "",
              "| Element | Fluid at stop (°C) | C (J/(m K)) | UA (W/(m K)) | tau, lumped (min) | tau, default (min) |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {r['element']} | {r['initial_fluid_C']:.0f} | {r['heat_capacity_J_per_mK']:.0f} | {r['ua_W_per_mK']:.3f} | "
              f"{r['tau_lumped_min']:.0f} | {r['tau_default_min']:.0f} |" for r in st]
    lines += ["", "## Envelope defect: one-dimensional surfaces vs. two-dimensional section", "",
              f"Boundary conditions: {d2['boundary']}.", "",
              "| | Insulated wall (°C) | Void centre (°C) | Contrast (K) |", "|---|---|---|---|",
              f"| 1-D (as in EnergyPlus) | {d2['one_d']['wall']} | {d2['one_d']['void']} | {d2['one_d']['contrast']} |",
              f"| 2-D section | {d2['two_d']['far_wall']} | {d2['two_d']['void_centre']} | {d2['two_d']['contrast']} |", "",
              f"2-D/1-D contrast at the void centre: {d2['centre_contrast_ratio_2d_to_1d']}. The contrast falls from 90 % to 10 % "
              f"between {d2['edge_90_to_10_percent_m'][0]} m and {d2['edge_90_to_10_percent_m'][1]} m from the centre "
              "(edge at 0.40 m)."]
    out = {"elements": {k: [list(map(float, r)) for r in v] for k, v in el.items()}, "worst_abs_difference_K": worst,
           "stagnation": st, "defect_2d": d2}
    (ROOT / "case_studies" / "benchmark.json").write_text(encoding="utf-8", data=json.dumps(out, indent=2, ensure_ascii=False))
    (ROOT / "docs" / "benchmark.md").write_text(encoding="utf-8", data="\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
