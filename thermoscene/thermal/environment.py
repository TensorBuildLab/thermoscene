"""Boundary conditions: outdoor environment (sun, sky) and per-space thermal environment series."""
from __future__ import annotations

import numpy as np

from ..ontology import Provenance

MONTH_START = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30])


def site(model, epw_path) -> dict:
    """Latitude, longitude, time zone of the simulated weather: EnergyPlus uses the weather file's LOCATION for
    weather-file run periods, whatever Site:Location says; Site:Location only without a weather file."""
    if epw_path:
        with open(epw_path, encoding="latin-1") as f:
            h = f.readline().split(",")
        return dict(lat=float(h[6]), lon=float(h[7]), tz=float(h[8]))
    loc = next(iter(model.objs("Site:Location").values()), {})
    return dict(lat=float(loc.get("latitude", 0.0)), lon=float(loc.get("longitude", 0.0)), tz=float(loc.get("time_zone", 0.0)))


def sun_altitude(time, site_, step_minutes) -> np.ndarray:
    """Solar altitude (rad), standard time, at the middle of each reporting interval."""
    n = MONTH_START[time.Month.to_numpy() - 1] + time.Day.to_numpy()
    t = time.Hour.to_numpy() + time.Minute.to_numpy() / 60 - step_minutes / 120
    b = 2 * np.pi * (n - 81) / 364
    eot = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)
    solar_t = t + (4 * (site_["lon"] - 15 * site_["tz"]) + eot) / 60
    ha = np.radians(15 * (solar_t - 12))
    dec = np.radians(23.45 * np.sin(2 * np.pi * (284 + n) / 365))
    lat = np.radians(site_["lat"])
    return np.arcsin(np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha))


def outdoor(results, model, epw) -> dict:
    s = site(model, epw)
    alt = sun_altitude(results.time, s, results.step_minutes)
    dni = results.series("Site Direct Solar Radiation Rate per Area", "Environment")
    dhi = results.series("Site Diffuse Solar Radiation Rate per Area", "Environment")
    return dict(T_out=results.series("Site Outdoor Air Drybulb Temperature", "Environment"),
                T_sky=results.series("Site Sky Temperature", "Environment"),
                T_dew=results.series("Site Outdoor Air Dewpoint Temperature", "Environment"),
                I_h=dni * np.maximum(np.sin(alt), 0) + dhi, site=s)


def assign_space_environments(scene, results, out_env) -> None:
    for sp in list(scene.things.values()):
        if sp.cls == "Outdoors":
            scene.set_series(sp.id, "airTemperature", out_env["T_out"], Provenance.SIMULATED, "Site Outdoor Air Drybulb")
            scene.set_series(sp.id, "reflectedTemperature", 0.5 * (out_env["T_sky"] + out_env["T_out"]),
                             Provenance.HEURISTIC, "half sky, half surroundings at air temperature")
            scene.set_series(sp.id, "backgroundTemperature", out_env["T_sky"], Provenance.SIMULATED, "Site Sky Temperature")
            scene.set_series(sp.id, "horizontalSolar", out_env["I_h"], Provenance.PHYSICS, "DNI*sin(altitude)+DHI")
            scene.set_series(sp.id, "dewPointTemperature", out_env["T_dew"], Provenance.SIMULATED, "Site dew point")
        elif getattr(sp, "sim_zone", None):
            t_air = results.series("Zone Mean Air Temperature", sp.sim_zone)
            mrt = results.series("Zone Mean Radiant Temperature", sp.sim_zone)
            scene.set_series(sp.id, "airTemperature", t_air, Provenance.SIMULATED, "Zone Mean Air Temperature")
            scene.set_series(sp.id, "meanRadiantTemperature", mrt, Provenance.SIMULATED, "Zone Mean Radiant Temperature")
            scene.set_series(sp.id, "reflectedTemperature", mrt, Provenance.PHYSICS, "diffuse surroundings ~ MRT")
            scene.set_series(sp.id, "backgroundTemperature", mrt, Provenance.PHYSICS, "diffuse surroundings ~ MRT")
