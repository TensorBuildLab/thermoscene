"""Read EnergyPlus SQLite output into numpy series on a common time axis (warm-up excluded)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


class Results:
    def __init__(self, sql_path, step_minutes: int | None = None) -> None:
        p = Path(sql_path)
        if not p.is_file() or p.stat().st_size == 0:          # sqlite3.connect would create an empty database
            raise FileNotFoundError(f"no EnergyPlus SQLite output at {p}")
        con = sqlite3.connect(str(p))
        self.con = con
        try:    # EnergyPlus does not index ReportData by variable; without it every series read scans the table
            con.execute("create index if not exists ts_report_data_dict on ReportData(ReportDataDictionaryIndex)")
            con.commit()
        except sqlite3.Error:
            pass
        ep = pd.read_sql("select EnvironmentPeriodIndex, EnvironmentName, EnvironmentType from EnvironmentPeriods", con)
        envs = dict(zip(ep.EnvironmentPeriodIndex, ep.EnvironmentName))
        weather_envs = set(ep[ep.EnvironmentType == 3].EnvironmentPeriodIndex) or set(ep.EnvironmentPeriodIndex)
        t = pd.read_sql("""select TimeIndex, Month, Day, Hour, Minute, Interval, EnvironmentPeriodIndex env
                           from Time where (WarmupFlag = 0 or WarmupFlag is null) and Interval is not null
                           and IntervalType = -1 order by TimeIndex""", con)
        if t.empty:   # older files: no IntervalType = -1 (zone timestep) rows
            t = pd.read_sql("""select TimeIndex, Month, Day, Hour, Minute, Interval, EnvironmentPeriodIndex env
                               from Time where (WarmupFlag = 0 or WarmupFlag is null) order by TimeIndex""", con)
        t = t[t.env.isin(weather_envs)]                    # weather-file run periods only (no design days)
        step = step_minutes or int(t.Interval.mode().iloc[0])
        t = t[t.Interval == step]                           # zone time steps only (no HVAC sub-steps)
        self.time = t.reset_index(drop=True)
        self.env_names = {e: envs[e] for e in self.time.env.unique()}
        self.labels = [f"{envs[e]} {m:02d}-{d:02d} {h:02d}:{mi:02d}"
                       for m, d, h, mi, e in zip(t.Month, t.Day, t.Hour, t.Minute, t.env)]
        self.n = len(self.labels)
        self._tindex = pd.Series(np.arange(self.n), index=self.time.TimeIndex.to_numpy())
        self.step_minutes = int(self.time.Interval.mode().iloc[0]) if self.n else 0
        dd = pd.read_sql("select ReportDataDictionaryIndex idx, KeyValue key, Name name, ReportingFrequency freq "
                         "from ReportDataDictionary", con)
        dd["k"] = dd.key.fillna("").str.upper(); dd["v"] = dd.name.str.upper()
        self.dict = dd
        self._cache: dict = {}

    def keys(self, variable: str) -> list[str]:
        return sorted(self.dict[self.dict.v == variable.upper()].k)

    def has(self, variable: str, key: str) -> bool:
        return not self.dict[(self.dict.v == variable.upper()) & (self.dict.k == key.upper())].empty

    def series(self, variable: str, key: str, default=None) -> np.ndarray:
        ck = (variable.upper(), key.upper())
        if ck in self._cache:
            return self._cache[ck]
        row = self.dict[(self.dict.v == ck[0]) & (self.dict.k == ck[1])]
        if len(row) > 1:                                    # same variable requested at several frequencies
            ts = row[row.freq.str.contains("Timestep", case=False, na=False)]
            row = ts if not ts.empty else row
        if row.empty:
            if default is not None:
                return np.full(self.n, default, float)
            raise KeyError(f"no output '{variable}' for key '{key}'")
        v = pd.read_sql(f"select TimeIndex, Value from ReportData where ReportDataDictionaryIndex = {int(row.idx.iloc[0])}", self.con)
        v = v[v.TimeIndex.isin(self._tindex.index)]
        out = np.full(self.n, np.nan)
        out[self._tindex[v.TimeIndex].to_numpy()] = v.Value.to_numpy()
        self._cache[ck] = out
        return out

    def component_sizes(self) -> dict[str, dict[str, float]]:
        cs = pd.read_sql("select CompType, CompName, Description, Value from ComponentSizes", self.con)
        out: dict[str, dict[str, float]] = {}
        for _, r in cs.iterrows():
            out.setdefault(r.CompName.upper(), {})[" ".join(str(r.Description).split())] = float(r.Value)
        return out
