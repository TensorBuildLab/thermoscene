"""
Surface-temperature models for generated elements, and assignment of all thermal time series.

A model is a function (element, fluid_temperature, ctx) -> (surface_temperature, provenance, note).
Register new models with @model("name"); elements select one by `element.thermal_model`.
"""
from __future__ import annotations

import numpy as np

from ..ontology import Element, Provenance, Surface

MODELS = {}


def model(name):
    def deco(f):
        MODELS[name] = f
        return f
    return deco


# ------------------------------------------------------------------ context helpers
class Ctx:
    def __init__(self, scene, results, cfg, out_env):
        self.scene, self.r, self.cfg, self.env = scene, results, cfg, out_env
        th = cfg["thermal"]
        self.h = th["film_coefficients"]; self.th = th
        self.k_ins = th["insulation"]["conductivity"]
        self.period_start = np.r_[True, results.time.env.to_numpy()[1:] != results.time.env.to_numpy()[:-1]]

    def mat(self, e):
        m = self.cfg["materials"][e.material]
        return m["emissivity"], m["solar_absorptance"]

    def sol_air(self, alpha, eps, hfrac=None, sfrac=None):
        sa = self.th["sol_air"]
        hfrac = sa["vertical_solar_fraction"] if hfrac is None else hfrac
        sfrac = sa["vertical_sky_fraction"] if sfrac is None else sfrac
        h_o, h_r = self.h["outdoor"], self.h["radiative"]
        return self.env["T_out"] + alpha * hfrac * self.env["I_h"] / h_o - eps * h_r * sfrac * (self.env["T_out"] - self.env["T_sky"]) / h_o

    def outdoors(self, e):
        return self.scene.get(e.space).cls == "Outdoors"

    def ambient(self, e):
        """Effective surroundings temperature of an element (sol-air outdoors, space air indoors)."""
        if self.outdoors(e):
            eps, alpha = self.mat(e)
            return self.sol_air(alpha, eps)
        return self.scene.series[(e.space, "airTemperature")]

    def ambient_air(self, e):
        return self.env["T_out"] if self.outdoors(e) else self.scene.series[(e.space, "airTemperature")]

    def h_out(self, e):
        return self.h["outdoor"] if self.outdoors(e) else self.h["plenum"]

    def signal(self, spec, default=0.0):
        if spec is None:
            return np.full(self.r.n, default)
        if isinstance(spec[0], (list, tuple)):
            return sum(self.signal(s, default) for s in spec)
        var, key = spec
        return np.nan_to_num(self.r.series(var, key, default=default))


def stagnate(T, flow, amb, tau, starts):
    out = np.array(T, float)
    for i in range(len(out)):
        if flow[i] <= 0:
            prev = amb[i] if starts[i] else out[i - 1]
            out[i] = amb[i] + (prev - amb[i]) * np.exp(-1.0 / tau)
    return out


def fluid(e: Element, ctx: Ctx):
    """Fluid temperature inside an element, with stagnant-fluid relaxation when its flow stops."""
    if e.stream_node is None:
        return None, None
    if e.stream_node.startswith("space:"):
        return ctx.scene.series[(e.stream_node[6:], "airTemperature")], None
    T = ctx.r.series("System Node Temperature", e.stream_node)
    flow = ctx.r.series("System Node Mass Flow Rate", e.attrs.get("flow_node", e.stream_node)) * e.attrs.get("flow_scale", 1.0)
    kind = e.attrs.get("stagnation")
    if kind:
        tau_steps = ctx.th["stagnation_tau_min"][kind] / ctx.r.step_minutes
        T = stagnate(T, np.nan_to_num(flow), ctx.ambient_air(e), tau_steps, ctx.period_start)
    return T, flow


# ------------------------------------------------------------------ models
@model("insulated_flat")
def _(e, Tf, c):
    Ta, h_o = c.ambient(e), c.h_out(e)
    R = e.thermal_params["thickness"] / c.k_ins
    return Ta - (Ta - Tf) * (1 / h_o) / (1 / c.h["inside_air"] + R + 1 / h_o), Provenance.PHYSICS, \
        f"1-D conduction through {e.thermal_params['thickness'] * 1000:.0f} mm insulation"


@model("insulated_pipe")
def _(e, Tf, c):
    Ta, h_o = c.ambient(e), c.h_out(e)
    r1 = e.thermal_params["od"] / 2; r2 = r1 + e.thermal_params["thickness"]
    R = r2 * np.log(r2 / r1) / c.k_ins
    return Ta - (Ta - Tf) * (1 / h_o) / (R + 1 / h_o), Provenance.PHYSICS, \
        f"radial conduction, {e.thermal_params['thickness'] * 1000:.0f} mm on {e.thermal_params['od'] * 1000:.0f} mm pipe"


@model("bare_duct")
def _(e, Tf, c):
    Ta, h_o, h_i = c.ambient(e), c.h_out(e), c.h["inside_air"]
    return Tf + (Ta - Tf) * (1 / h_i) / (1 / h_i + 1 / h_o), Provenance.PHYSICS, "bare sheet metal, film resistances"


@model("bare_pipe")
def _(e, Tf, c):
    return Tf + (c.ambient(e) - Tf) * c.th["bare_pipe_coupling"], Provenance.HEURISTIC, \
        "bare metal, fixed film-resistance ratio (water side dominates)"


@model("ambient")
def _(e, Tf, c):
    return (Tf if Tf is not None else c.ambient_air(e)), Provenance.PHYSICS, "at surrounding air temperature"


@model("sol_air")
def _(e, Tf, c):
    eps, alpha = c.mat(e)
    p = e.thermal_params
    return c.sol_air(alpha, eps, p.get("horizontal_fraction", 1.0), p.get("sky_fraction", 1.0)), Provenance.HEURISTIC, \
        "steady sol-air: air + absorbed solar - sky long-wave"


@model("casing")
def _(e, Tf, c):
    Ta = c.ambient(e)
    if Tf is None:
        return Ta, Provenance.HEURISTIC, "sol-air (no internal stream)"
    k = e.thermal_params["coupling"]
    return Ta + (Tf - Ta) * k, Provenance.HEURISTIC, f"sol-air + {k:.0%} coupling to internal air"


@model("louver")
def _(e, Tf, c):
    return c.env["T_out"] + c.th["louver_sol_air_fraction"] * (c.ambient(e) - c.env["T_out"]), Provenance.HEURISTIC, \
        "blades in the outdoor-air stream"


@model("compressor")
def _(e, Tf, c):
    plr = np.clip(c.signal(e.attrs.get("plr")), 0, None)
    return c.ambient(e) + c.th["compressor_rise_per_plr"] * plr, Provenance.HEURISTIC, "sol-air + rise x part-load ratio"


def _discharge_rise(e, c):
    q = np.clip(c.signal(e.attrs.get("heat_rejection")), 0, None)
    air = c.th["condenser_air"]
    return q / (air["density"] * e.attrs["condenser_airflow_m3s"] * air["cp"]), q > 0


@model("condenser_coil")
def _(e, Tf, c):
    dT, on = _discharge_rise(e, c)
    return np.where(on, c.env["T_out"] + c.th["condenser_coil_fraction"] * dT, c.ambient(e)), Provenance.HEURISTIC, \
        "outdoor + fraction of condenser air temperature rise (from simulated heat rejection)"


@model("condenser_fan")
def _(e, Tf, c):
    dT, on = _discharge_rise(e, c)
    eps, alpha = c.mat(e)
    return np.where(on, c.env["T_out"] + dT, c.sol_air(alpha, eps, 1.0, 1.0)), Provenance.HEURISTIC, \
        "fan guard at condenser discharge-air temperature when running"


@model("motor")
def _(e, Tf, c):
    on = c.signal(e.attrs.get("power")) > 0
    return np.where(on, c.ambient(e) + c.th["motor_rise"], c.ambient(e)), Provenance.HEURISTIC, "motor rise when running"


@model("diffuser_face")
def _(e, Tf, c):
    p = c.th["diffuser"]
    t_ceil = c.scene.series[(e.attrs["ceiling_surface"], "surfaceTemperature")]
    _, flow = fluid(e, c)
    ff = np.clip(np.nan_to_num(flow) / e.attrs["design_flow_kg_s"], 0, 1.2)
    eta = p["eta_max"] * np.where(ff > 0, ff, 0) ** p["flow_exp"]
    c.scene.set_series(e.id, "jetStrength", p["jet_strength_max"] * np.sqrt(ff), Provenance.HEURISTIC, "ceiling-jet strength")
    c.scene.set_series(e.id, "jetLength", p["jet_len0"] + p["jet_len1"] * ff, Provenance.HEURISTIC, "ceiling-jet decay length")
    Tsup = c.r.series("System Node Temperature", e.stream_node)
    return t_ceil + (Tsup - t_ceil) * eta, Provenance.HEURISTIC, "ceiling + (supply - ceiling) * eta_max * flowfrac^n"


@model("return_grille")
def _(e, Tf, c):
    t_ceil = c.scene.series[(e.attrs["ceiling_surface"], "surfaceTemperature")]
    mix = c.th["return_grille_mix"]
    on = np.nan_to_num(c.r.series("System Node Mass Flow Rate", e.stream_node)) > 0
    Tr = c.r.series("System Node Temperature", e.stream_node)
    return np.where(on, mix * Tr + (1 - mix) * t_ceil, t_ceil), Provenance.HEURISTIC, "mix of return air and ceiling"


# ------------------------------------------------------------------ internal gains (generators/internal_gains.py)
def _power_fraction(e, c):
    return np.clip(c.signal(e.attrs.get("power")), 0, None) / max(float(e.attrs.get("design_power_w") or 1.0), 1e-9)


@model("luminaire")
def _(e, Tf, c):
    t_ceil = c.scene.series[(e.attrs["ceiling_surface"], "surfaceTemperature")]
    return t_ceil + c.cfg["internal_gains"]["luminaire"]["rise_k"] * _power_fraction(e, c), Provenance.HEURISTIC, \
        "ceiling + lens rise x simulated lighting power fraction"


@model("plug_load")
def _(e, Tf, c):
    return c.ambient_air(e) + c.cfg["internal_gains"]["monitor_rise_k"] * _power_fraction(e, c), Provenance.HEURISTIC, \
        "air + rise x simulated plug-load power fraction"


def _occupant_state(e, c):
    """Simulated air and mean radiant temperature, metabolic rate (W/m2) and ASHRAE 55 clothing of the zone."""
    from ..generators.internal_gains import ashrae55_clothing
    g = c.cfg["internal_gains"]["occupant"]
    sp = c.scene.get(e.space)
    ta = c.scene.series[(sp.id, "airTemperature")]
    tr = c.scene.series.get((sp.id, "meanRadiantTemperature"), ta)
    count = c.signal(e.attrs.get("people"))
    heat = c.signal(e.attrs.get("people_heat"))
    per_person = np.where(count > 1e-6, heat / np.maximum(count, 1e-6), g["default_activity_w"])
    met = per_person / g["body_area_m2"]
    t = c.r.time
    day = t.Month.to_numpy() * 100 + t.Day.to_numpy()
    six = (t.Hour.to_numpy() == 6) & (t.Minute.to_numpy() == 0)
    t6 = {d: v for d, v, s in zip(day, c.env["T_out"], six) if s}
    t_6am = np.array([t6.get(d, np.nan) for d in day])
    daily_min = {d: np.nanmin(c.env["T_out"][day == d]) for d in np.unique(day)}
    t_6am = np.where(np.isfinite(t_6am), t_6am, [daily_min[d] for d in day])
    return ta, tr, met, ashrae55_clothing(t_6am), g


@model("occupant_clothing")
def _(e, Tf, c):
    from ..generators.internal_gains import clothing_temperature
    ta, tr, met, clo, g = _occupant_state(e, c)
    return clothing_temperature(ta, tr, met, clo, g["air_velocity_ms"]), Provenance.HEURISTIC, \
        "ISO 7730 clothing surface temperature; simulated air, MRT and activity; ASHRAE 55 dynamic clothing"


@model("occupant_skin")
def _(e, Tf, c):
    from ..generators.internal_gains import skin_temperature
    _, _, met, _, _ = _occupant_state(e, c)
    return skin_temperature(met), Provenance.HEURISTIC, "ISO 7730 bare-skin limit, 35.7 - 0.028 M, from the simulated activity"


# ------------------------------------------------------------------ assignment
def assign(scene, ctx: Ctx) -> None:
    for s in scene.of(Surface):
        var = "Surface Inside Face Temperature" if s.attrs.get("face") == "inside" else "Surface Outside Face Temperature"
        scene.set_series(s.id, "surfaceTemperature", ctx.r.series(var, s.sim_surface), Provenance.SIMULATED, var)
    elements = scene.of(Element)
    # diffusers/grilles depend on ceiling surfaces only; everything else on fluids
    for e in elements:
        Tf, flow = fluid(e, ctx)
        if Tf is not None:
            scene.set_series(e.id, "fluidTemperature", Tf, Provenance.SIMULATED if not e.attrs.get("stagnation") else Provenance.PHYSICS,
                             f"EnergyPlus node {e.stream_node}" + (" with stagnant-fluid relaxation" if e.attrs.get("stagnation") else ""))
        if flow is not None:
            scene.set_series(e.id, "fluidMassFlow", flow, Provenance.SIMULATED, "node mass flow x share")
        Ts, lvl, note = MODELS[e.thermal_model](e, Tf, ctx)
        scene.set_series(e.id, "surfaceTemperature", np.asarray(Ts, float), lvl, note)
        if e.attrs.get("condensation_check") and ctx.outdoors(e):
            scene.set_series(e.id, "condensationRisk", Ts < ctx.env["T_dew"], Provenance.PHYSICS, "surface below dew point")
