"""Physical plausibility of the built case scenes at every time step (skipped when a case has not been built).

- A passive element surface (insulated or bare duct and pipe in a plenum) lies between its fluid and the surrounding air.
- A wrapped duct surface is never further from its fluid than the bare segment on the same air stream would be, i.e.
  the insulation gap is always closer to the fluid temperature than the wrapped duct.
- A supply diffuser face lies between the ceiling and the supply-air temperature.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = ["A_single_room", "B_hvac_system", "C_doe_medium_office"]
PASSIVE = {"insulated_flat", "insulated_pipe", "bare_duct", "bare_pipe"}
TOL = 0.01      # K


def _stages():
    from pxr import Usd
    for case in CASES:
        usd = ROOT / "case_studies" / case / "output" / f"{case}.usda"
        if usd.exists():
            yield case, Usd.Stage.Open(str(usd))


def _series(ser, prim, name, n):
    return np.array([ser.get(prim, name, tc) for tc in range(n)], float)


def _space(stage, prim):
    path = prim.GetPath()
    while path.pathElementCount > 2:            # /World/<Space>/...
        path = path.GetParentPath()
    return stage.GetPrimAtPath(path)


def test_passive_elements_lie_between_fluid_and_surrounding_air():
    from thermoscene.render.shading import Series, labels, thermal_prims
    checked = 0
    for case, st in _stages():
        ser, n = Series(st), len(labels(st))
        for p in thermal_prims(st):
            model = p.GetAttribute("thermal:model").Get() if p.HasAttribute("thermal:model") else None
            space = _space(st, p)
            if model not in PASSIVE or space.GetName() == "Outdoors":
                continue
            ts = _series(ser, p, "thermal:surfaceTemperature", n)
            tf = _series(ser, p, "mep:fluidTemperature", n)
            ta = _series(ser, space, "thermal:airTemperature", n)
            lo, hi = np.minimum(tf, ta) - TOL, np.maximum(tf, ta) + TOL
            bad = np.flatnonzero((ts < lo) | (ts > hi))
            assert bad.size == 0, (case, str(p.GetPath()), int(bad[0]), ts[bad[0]], tf[bad[0]], ta[bad[0]])
            checked += 1
    if not checked:
        print("skipped: no built cases")


def test_insulation_gap_is_closer_to_the_fluid_than_the_wrapped_duct():
    from thermoscene.render.shading import Series, labels, thermal_prims
    for case, st in _stages():
        ser, n = Series(st), len(labels(st))
        prims = thermal_prims(st)
        model = {p: (p.GetAttribute("thermal:model").Get() if p.HasAttribute("thermal:model") else None) for p in prims}
        node = {p: (p.GetAttribute("mep:streamNode").Get() if p.HasAttribute("mep:streamNode") else None) for p in prims}
        for gap in (p for p in prims if model[p] == "bare_duct"):
            wrapped = [p for p in prims if model[p] == "insulated_flat" and node[p] == node[gap]]
            if not wrapped:
                continue
            tf = _series(ser, gap, "mep:fluidTemperature", n)
            d_gap = np.abs(_series(ser, gap, "thermal:surfaceTemperature", n) - tf)
            for w in wrapped:
                d_w = np.abs(_series(ser, w, "thermal:surfaceTemperature", n) - _series(ser, w, "mep:fluidTemperature", n))
                assert np.all(d_gap <= d_w + TOL), (case, str(gap.GetPath()), str(w.GetPath()))


def test_diffuser_face_lies_between_ceiling_and_supply_air():
    from thermoscene.render.shading import Series, labels, thermal_prims
    for case, st in _stages():
        ser, n = Series(st), len(labels(st))
        prims = thermal_prims(st)
        for d in (p for p in prims if p.HasAttribute("thermal:model") and p.GetAttribute("thermal:model").Get() == "diffuser_face"):
            space = _space(st, d)
            ceilings = [p for p in prims if _space(st, p) == space and p.HasAttribute("ts:surfaceType")
                        and p.GetAttribute("ts:surfaceType").Get() == "Ceiling"]
            if not ceilings:
                continue
            tc = np.array([_series(ser, c, "thermal:surfaceTemperature", n) for c in ceilings])
            tsup = _series(ser, d, "mep:fluidTemperature", n)
            td = _series(ser, d, "thermal:surfaceTemperature", n)
            lo = np.minimum(tsup, tc.min(axis=0)) - TOL
            hi = np.maximum(tsup, tc.max(axis=0)) + TOL
            bad = np.flatnonzero((td < lo) | (td > hi))
            assert bad.size == 0, (case, str(d.GetPath()), int(bad[0]))
