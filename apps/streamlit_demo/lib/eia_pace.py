from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .paths import GRID_CACHE_DIR

EIA_BASE = "https://api.eia.gov/v2"
BA = "PACE"
# EIA frequency=hourly timestamps are UTC; PACE operates on Mountain time.
PACE_TZ = "America/Denver"
CACHE_VERSION = "eia_local_v2"

FUEL_LABELS = {
    "COL": "coal",
    "NG": "natural_gas",
    "SUN": "solar",
    "WND": "wind",
    "WAT": "hydro",
    "NUC": "nuclear",
    "OTH": "other",
    "OIL": "oil",
}

# Rough average CI (kgCO2/MWh) for fallback intensity from fuel mix
FUEL_CI = {
    "coal": 1000.0,
    "natural_gas": 450.0,
    "solar": 0.0,
    "wind": 0.0,
    "hydro": 0.0,
    "nuclear": 0.0,
    "other": 500.0,
    "oil": 800.0,
}


def _api_key() -> str | None:
    return os.getenv("EIA_API_KEY") or os.getenv("EIA_APIKEY")


def _get_paginated(url: str, params: dict) -> list[dict]:
    key = _api_key()
    if not key:
        raise RuntimeError("EIA_API_KEY missing — add it to .env")
    params = {**params, "api_key": key}
    rows: list[dict] = []
    offset = 0
    length = 5000
    while True:
        q = {**params, "offset": offset, "length": length}
        r = requests.get(url, params=q, timeout=60)
        r.raise_for_status()
        payload = r.json()
        data = (payload.get("response") or {}).get("data") or []
        total = int((payload.get("response") or {}).get("total") or 0)
        rows.extend(data)
        offset += length
        if offset >= total or not data:
            break
        time.sleep(0.2)
    return rows


def _utc_window(start: str, end: str) -> tuple[str, str]:
    """Pad ±1 calendar day so Mountain-local days are complete after UTC convert."""
    s = date.fromisoformat(start) - timedelta(days=1)
    e = date.fromisoformat(end) + timedelta(days=1)
    return s.isoformat(), e.isoformat()


def _to_pace_local(period: pd.Series) -> pd.Series:
    """EIA hourly periods are UTC; convert to America/Denver wall clock."""
    ts = pd.to_datetime(period, utc=True)
    return ts.dt.tz_convert(PACE_TZ)


def _local_date_hour(period_local: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=period_local.index)
    out["period"] = period_local
    out["date"] = period_local.dt.strftime("%Y-%m-%d")
    # hour-beginning local → hour_ending 1..24
    out["hour_ending"] = period_local.dt.hour + 1
    return out


def fetch_region_demand(start: str, end: str) -> pd.DataFrame:
    """Hourly PACE demand on America/Denver calendar days. start/end YYYY-MM-DD."""
    url = f"{EIA_BASE}/electricity/rto/region-data/data/"
    utc_start, utc_end = _utc_window(start, end)
    params = {
        "frequency": "hourly",  # UTC
        "data[0]": "value",
        "facets[respondent][]": BA,
        "facets[type][]": "D",
        "start": f"{utc_start}T00",
        "end": f"{utc_end}T23",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }
    rows = _get_paginated(url, params)
    if not rows:
        return pd.DataFrame(columns=["period", "demand_mw", "date", "hour_ending"])
    df = pd.DataFrame(rows)
    df["demand_mw"] = pd.to_numeric(df["value"], errors="coerce")
    local = _local_date_hour(_to_pace_local(df["period"]))
    df = pd.concat([local, df[["demand_mw"]]], axis=1)
    df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    df["period"] = df["period"].dt.tz_localize(None)
    return (
        df[["period", "date", "hour_ending", "demand_mw"]]
        .dropna()
        .sort_values(["date", "hour_ending"])
        .reset_index(drop=True)
    )


def fetch_fuel_type(start: str, end: str) -> pd.DataFrame:
    """Hourly PACE generation by fuel type (MW), America/Denver local day."""
    url = f"{EIA_BASE}/electricity/rto/fuel-type-data/data/"
    utc_start, utc_end = _utc_window(start, end)
    params = {
        "frequency": "hourly",  # UTC
        "data[0]": "value",
        "facets[respondent][]": BA,
        "start": f"{utc_start}T00",
        "end": f"{utc_end}T23",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }
    rows = _get_paginated(url, params)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["mw"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    local = _local_date_hour(_to_pace_local(df["period"]))
    df = pd.concat([local, df[["mw", "fueltype"]]], axis=1)
    df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    df["fuel"] = df["fueltype"].map(FUEL_LABELS).fillna(df["fueltype"].str.lower())
    wide = (
        df.groupby(["date", "hour_ending", "fuel"], as_index=False)["mw"]
        .sum()
        .pivot(index=["date", "hour_ending"], columns="fuel", values="mw")
        .fillna(0.0)
        .reset_index()
    )
    fuel_cols = [c for c in wide.columns if c not in ("date", "hour_ending")]
    wide["gen_total_mw"] = wide[fuel_cols].sum(axis=1)
    renew = [c for c in ("solar", "wind", "hydro") if c in wide.columns]
    wide["renewable_mw"] = wide[renew].sum(axis=1) if renew else 0.0
    wide["renewable_share"] = wide["renewable_mw"] / wide["gen_total_mw"].clip(
        lower=1e-6
    )
    ci = 0.0
    for fuel, intensity in FUEL_CI.items():
        if fuel in wide.columns:
            ci = ci + wide[fuel] * intensity
    wide["ci_kg_per_mwh"] = ci / wide["gen_total_mw"].clip(lower=1e-6)
    return wide.sort_values(["date", "hour_ending"]).reset_index(drop=True)

def cache_paths(date: str, cache_dir: Path = GRID_CACHE_DIR) -> dict[str, Path]:
    d = cache_dir / date
    return {
        "dir": d,
        "demand": d / "demand.parquet",
        "fuel": d / "fuel_mix.parquet",
    }


def load_nearest_fuel(
    day: str,
    *,
    cache_dir: Path = GRID_CACHE_DIR,
    lookback_days: int = 7,
) -> tuple[pd.DataFrame, str]:
    """
    Nearest prior EIA fuel_mix for CI when the target day has no fuel cache.
    Returns (fuel_df, source_date) or (empty, "").
    """
    try:
        cur = date.fromisoformat(day)
    except ValueError:
        return pd.DataFrame(), ""
    for back in range(1, lookback_days + 1):
        prior = (cur - timedelta(days=back)).isoformat()
        path = cache_dir / prior / "fuel_mix.parquet"
        marker = cache_dir / prior / "source.txt"
        if not path.is_file():
            continue
        if marker.is_file() and marker.read_text().strip() != "eia":
            continue
        fuel = pd.read_parquet(path)
        if fuel.empty:
            continue
        fuel = fuel.copy()
        fuel["date"] = day
        return fuel, prior
    return pd.DataFrame(), ""


def synthetic_pace_day(date: str) -> dict[str, pd.DataFrame]:
    """
    Labeled offline PACE proxy when EIA_API_KEY is missing.
    Diurnal shape: solar midday belly, gas/coal evening ramp — not live EIA.
    """
    hours = np.arange(1, 25)
    # ~4.2–5.5 GW system shape
    base = 3800 + 400 * np.sin((hours - 6) / 24 * 2 * np.pi)
    evening = 900 * np.exp(-0.5 * ((hours - 20) / 2.2) ** 2)
    demand_mw = base + evening
    solar = np.clip(2200 * np.sin(np.clip((hours - 6) / 14 * np.pi, 0, np.pi)), 0, None)
    if date.startswith("2026-10") or date.startswith("2026-02") or date.startswith(
        "2026-03"
    ):
        solar *= 0.55
    wind = 350 + 80 * np.sin((hours + 3) / 24 * 2 * np.pi)
    hydro = np.full(24, 280.0)
    coal = 900 + 200 * (evening / (evening.max() + 1e-6))
    gas = np.clip(demand_mw - solar - wind - hydro - coal, 200, None)
    demand = pd.DataFrame(
        {
            "period": pd.to_datetime(date) + pd.to_timedelta(hours - 1, unit="h"),
            "date": date,
            "hour_ending": hours,
            "demand_mw": demand_mw,
        }
    )
    fuel = pd.DataFrame(
        {
            "date": date,
            "hour_ending": hours,
            "solar": solar,
            "wind": wind,
            "hydro": hydro,
            "coal": coal,
            "natural_gas": gas,
            "nuclear": 0.0,
            "other": 50.0,
            "oil": 0.0,
        }
    )
    fuel_cols = [
        "solar",
        "wind",
        "hydro",
        "coal",
        "natural_gas",
        "nuclear",
        "other",
        "oil",
    ]
    fuel["gen_total_mw"] = fuel[fuel_cols].sum(axis=1)
    fuel["renewable_mw"] = fuel[["solar", "wind", "hydro"]].sum(axis=1)
    fuel["renewable_share"] = fuel["renewable_mw"] / fuel["gen_total_mw"]
    ci = 0.0
    for name, intensity in FUEL_CI.items():
        if name in fuel.columns:
            ci = ci + fuel[name] * intensity
    fuel["ci_kg_per_mwh"] = ci / fuel["gen_total_mw"]
    fuel.attrs["source"] = "synthetic_pace_proxy"
    demand.attrs["source"] = "synthetic_pace_proxy"
    return {"demand": demand, "fuel": fuel}


def load_or_fetch_day(date: str, cache_dir: Path = GRID_CACHE_DIR) -> dict[str, pd.DataFrame]:
    paths = cache_paths(date, cache_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    source = "eia"

    marker = paths["dir"] / "source.txt"
    version = paths["dir"] / "cache_version.txt"
    cache_ok = (
        paths["demand"].is_file()
        and paths["fuel"].is_file()
        and version.is_file()
        and version.read_text().strip() == CACHE_VERSION
    )
    try:
        if cache_ok:
            out["demand"] = pd.read_parquet(paths["demand"])
            out["fuel"] = pd.read_parquet(paths["fuel"])
            source = marker.read_text().strip() if marker.is_file() else "cached"
        else:
            demand = fetch_region_demand(date, date)
            fuel = fetch_fuel_type(date, date)
            demand.to_parquet(paths["demand"], index=False)
            fuel.to_parquet(paths["fuel"], index=False)
            source = "eia"
            marker.write_text(source + "\n")
            version.write_text(CACHE_VERSION + "\n")
            out["demand"] = demand
            out["fuel"] = fuel
    except Exception:
        if not _api_key():
            source = "synthetic_pace_proxy"
            syn = synthetic_pace_day(date)
            syn["demand"].to_parquet(paths["demand"], index=False)
            syn["fuel"].to_parquet(paths["fuel"], index=False)
            marker.write_text(source + "\n")
            version.write_text(CACHE_VERSION + "\n")
            out = syn
        else:
            raise

    out["source"] = source  # type: ignore[assignment]
    return out
