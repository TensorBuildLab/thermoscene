"""Prepare and run a user's EnergyPlus model, then convert the (expanded) model to epJSON."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .idf_text import IDFText, run_period

# Everything the pipeline may need. '*' keys keep the request model-independent.
OUTPUT_REQUESTS = [
    "Surface Inside Face Temperature", "Surface Outside Face Temperature",
    "Zone Mean Air Temperature", "Zone Mean Radiant Temperature",
    "System Node Temperature", "System Node Mass Flow Rate",
    "Zone Air Terminal VAV Damper Position", "Zone Air Terminal Sensible Cooling Rate",
    "Zone Air Terminal Sensible Heating Rate",
    "Chiller Part Load Ratio", "Chiller Electricity Rate", "Chiller Condenser Heat Transfer Rate",
    "Chiller Evaporator Cooling Rate", "Boiler Heating Rate", "Pump Electricity Rate", "Fan Electricity Rate",
    "Cooling Coil Total Cooling Rate", "Heating Coil Heating Rate",
    "Cooling Coil Electricity Rate", "Heating Coil Electricity Rate",
]
SITE_REQUESTS = ["Site Outdoor Air Drybulb Temperature", "Site Outdoor Air Dewpoint Temperature",
                 "Site Sky Temperature", "Site Direct Solar Radiation Rate per Area",
                 "Site Diffuse Solar Radiation Rate per Area"]


def find_energyplus(hint: str | None = None) -> str:
    for cand in (hint, os.environ.get("THERMOSCENE_ENERGYPLUS"), shutil.which("energyplus")):
        if cand and Path(cand).exists():
            return str(Path(cand).resolve())
        if cand and shutil.which(cand):
            return shutil.which(cand)
    raise FileNotFoundError("EnergyPlus not found: set THERMOSCENE_ENERGYPLUS or energyplus.executable in the config")


def energyplus_dir(exe: str) -> Path:
    return Path(exe).resolve().parent


def prepare(src_idf, dst_idf, run_periods=None, extra_objects=(), timesteps_per_hour=None) -> IDFText:
    idf = IDFText.read(src_idf)
    if run_periods:
        idf.remove("RunPeriod")
        for rp in run_periods:
            idf.append(run_period(rp["name"], rp["start"], rp["end"], rp.get("start_day_of_week", "UseWeatherFile")))
        sc = idf.find("SimulationControl")
        if not sc:
            idf.append("SimulationControl, Yes, Yes, Yes, No, Yes;")
        else:                                        # sizing calculations stay; run the weather-file periods only
            o = sc[0]
            o += [""] * max(0, 6 - len(o))
            o[4], o[5] = "No", "Yes"
    if timesteps_per_hour:
        idf.remove("Timestep")
        idf.append(f"Timestep, {int(timesteps_per_hour)};")
    if not idf.find("Output:SQLite"):
        idf.append("Output:SQLite, SimpleAndTabular;")
    for v in OUTPUT_REQUESTS:
        idf.append(f"Output:Variable, *, {v}, Timestep;")
    for v in SITE_REQUESTS:
        idf.append(f"Output:Variable, Environment, {v}, Timestep;")
    for raw in extra_objects:
        idf.append(raw)
    idf.write(dst_idf)
    return idf


def simulate(idf_path, epw, outdir, exe: str, expand: bool | None = None) -> dict:
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    if expand is None:
        expand = any(c.startswith("HVACTEMPLATE:") for c in IDFText.read(idf_path).classes())
    cmd = [exe, "-w", str(epw), "-d", str(outdir)] + (["-x"] if expand else []) + [str(idf_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    err = (outdir / "eplusout.err").read_text(encoding="utf-8", errors="ignore") if (outdir / "eplusout.err").exists() else ""
    if proc.returncode != 0 or "** Fatal **" in err:
        tail = "\n".join(l for l in err.splitlines() if "Severe" in l or "Fatal" in l or "~~~" in l)[-3000:]
        raise RuntimeError(f"EnergyPlus failed ({proc.returncode}):\n{tail or proc.stdout[-2000:]}")
    expanded = outdir / "eplusout.expidf"
    return dict(sql=outdir / "eplusout.sql", err=outdir / "eplusout.err",
                model_idf=expanded if expanded.exists() else Path(idf_path),
                warnings=err.count("** Warning **"), severe=err.count("** Severe  **"))


def to_epjson(idf_path, exe: str, outdir) -> Path:
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    src = outdir / "model_for_conversion.idf"
    shutil.copy(idf_path, src)
    subprocess.run([exe, "--convert-only", "-d", str(outdir), str(src)], capture_output=True, text=True, check=True)
    out = outdir / "model_for_conversion.epJSON"
    if not out.exists():
        raise RuntimeError("epJSON conversion failed")
    return out
