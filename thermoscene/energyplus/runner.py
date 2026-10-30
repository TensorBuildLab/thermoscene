"""Prepare and run a user's EnergyPlus model, then convert the (expanded) model to epJSON."""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from pathlib import Path

from .idf_text import IDFText, run_period

# Everything the pipeline may need. '*' keys keep the request model-independent.
OUTPUT_REQUESTS = [
    "Surface Inside Face Temperature", "Surface Outside Face Temperature",
    "Surface Inside Face Conduction Heat Transfer Rate",              # energy consequence of envelope faults
    "Surface Inside Face Conduction Heat Transfer Rate per Area",
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
# Object classes EnergyPlus only accepts after ExpandObjects has run
EXPAND_PREFIXES = ("HVACTEMPLATE:", "GROUNDHEATTRANSFER:")
FILE_FIELD = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}$")      # a field value that looks like a file name


def needs_expand(idf: IDFText) -> bool:
    return any(c.startswith(EXPAND_PREFIXES) for c in idf.classes())


def external_files(idf: IDFText, base_dir) -> list[Path]:
    """Files the model references by name (Schedule:File CSVs, window data files, ...) that exist relative to the
    model's folder or as absolute paths; they are simulation inputs too (part of the cache key)."""
    out = set()
    for o in idf.objects:
        for v in o[1:]:
            if v and FILE_FIELD.search(v):
                try:
                    f = Path(v) if Path(v).is_absolute() else Path(base_dir) / v
                    if f.is_file():
                        out.add(f.resolve())
                except (OSError, ValueError):
                    continue
    return sorted(out)


def err_tail(err: str, n: int = 3000) -> str:
    return "\n".join(line for line in err.splitlines() if "Severe" in line or "Fatal" in line or "~~~" in line)[-n:]


def find_energyplus(hint: str | None = None) -> str:
    for cand in (hint, os.environ.get("THERMOSCENE_ENERGYPLUS"), shutil.which("energyplus")):
        if cand and Path(cand).exists():
            return str(Path(cand).resolve())
        if cand and shutil.which(cand):
            return shutil.which(cand)
    raise FileNotFoundError("EnergyPlus not found: set THERMOSCENE_ENERGYPLUS or energyplus.executable in the config")


def energyplus_dir(exe: str) -> Path:
    return Path(exe).resolve().parent


@functools.lru_cache(maxsize=None)
def engine_version(exe: str) -> str:
    """'EnergyPlus 22.2.0' from `energyplus --version`; the install folder name if that fails."""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Version\s+([0-9][0-9.]*)", out)
        if m:
            return f"EnergyPlus {m.group(1).rstrip('.')}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"EnergyPlus ({Path(exe).parent.name})"


def prepare(src_idf, dst_idf, run_periods=None, extra_objects=(), timesteps_per_hour=None) -> IDFText:
    idf = IDFText.read(src_idf)
    if run_periods:
        idf.remove("RunPeriod")
        for rp in run_periods:
            idf.append(run_period(rp["name"], rp["start"], rp["end"], rp.get("start_day_of_week", "")))
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
    idf.remove("OutputControl:Files")                  # it can switch the SQLite output off; its defaults are all Yes
    for v in OUTPUT_REQUESTS:
        idf.append(f"Output:Variable, *, {v}, Timestep;")
    for v in SITE_REQUESTS:
        idf.append(f"Output:Variable, Environment, {v}, Timestep;")
    for raw in extra_objects:
        idf.append(raw)
    idf.write(dst_idf)
    return idf


def simulate(idf_path, epw, outdir, exe: str, expand: bool | None = None, cwd=None) -> dict:
    """Run EnergyPlus. `cwd` should be the folder of the user's model: EnergyPlus resolves relative file names
    (Schedule:File, window data files, ...) against its working directory and the folder of the IDF it runs."""
    outdir = Path(outdir).resolve(); outdir.mkdir(parents=True, exist_ok=True)
    idf_path, epw = Path(idf_path).resolve(), Path(epw).resolve()
    if expand is None:
        expand = needs_expand(IDFText.read(idf_path))
    cmd = [exe, "-w", str(epw), "-d", str(outdir)] + (["-x"] if expand else []) + [str(idf_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(cwd) if cwd else None)
    err = (outdir / "eplusout.err").read_text(encoding="utf-8", errors="ignore") if (outdir / "eplusout.err").exists() else ""
    if proc.returncode != 0 or "** Fatal **" in err:
        raise RuntimeError(f"EnergyPlus failed ({proc.returncode}):\n{err_tail(err) or proc.stdout[-2000:]}")
    sql = outdir / "eplusout.sql"
    if not sql.exists() or sql.stat().st_size == 0:
        raise RuntimeError(f"EnergyPlus wrote no SQLite output ({sql}); check Output:SQLite and "
                           f"OutputControl:Files in the model\n{err_tail(err)}")
    expanded = outdir / "eplusout.expidf"
    return dict(sql=outdir / "eplusout.sql", err=outdir / "eplusout.err",
                model_idf=expanded if expanded.exists() else Path(idf_path),
                warnings=err.count("** Warning **"), severe=err.count("** Severe  **"))


def to_epjson(idf_path, exe: str, outdir) -> Path:
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    src = outdir / "model_for_conversion.idf"
    shutil.copy(idf_path, src)
    proc = subprocess.run([exe, "--convert-only", "-d", str(outdir), str(src)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = outdir / "model_for_conversion.epJSON"
    if proc.returncode != 0 or not out.exists():
        errf = outdir / "eplusout.err"
        err = errf.read_text(encoding="utf-8", errors="ignore") if errf.exists() else ""
        raise RuntimeError(f"epJSON conversion of {Path(idf_path).name} failed ({proc.returncode}):\n"
                           f"{err_tail(err) or proc.stdout[-2000:]}")
    return out
