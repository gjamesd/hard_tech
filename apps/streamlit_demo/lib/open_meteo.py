from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from .paths import GRID_CACHE_DIR, MILLCREEK_LAT, MILLCREEK_LON

HOURLY_VARS = (
    "temperature_2m,shortwave_radiation,precipitation,snowfall"
)


def _hourly_frame(hourly: dict, date_fallback: str | None = None) -> pd.DataFrame:
    times = hourly.get("time") or []
    if not times:
        return pd.DataFrame(
            columns=[
                "date",
                "hour_ending",
                "temp_c",
                "shortwave_wm2",
                "precip_mm",
                "snowfall_cm",
            ]
        )
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(times),
            "temp_c": hourly.get("temperature_2m"),
            "shortwave_wm2": hourly.get("shortwave_radiation"),
            "precip_mm": hourly.get("precipitation"),
            "snowfall_cm": hourly.get("snowfall"),
        }
    )
    df["date"] = df["time"].dt.strftime("%Y-%m-%d")
    if date_fallback and df["date"].nunique() == 1:
        df["date"] = date_fallback
    df["hour_ending"] = df["time"].dt.hour + 1
    return df[
        [
            "date",
            "hour_ending",
            "temp_c",
            "shortwave_wm2",
            "precip_mm",
            "snowfall_cm",
        ]
    ]


def fetch_millcreek_range(start: str, end: str) -> pd.DataFrame:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": MILLCREEK_LAT,
        "longitude": MILLCREEK_LON,
        "start_date": start,
        "end_date": end,
        "hourly": HOURLY_VARS,
        "timezone": "America/Denver",
    }
    r = requests.get(url, params=params, timeout=90)
    r.raise_for_status()
    return _hourly_frame(r.json().get("hourly") or {})


def fetch_millcreek_day(date: str) -> pd.DataFrame:
    """Hourly weather for Millcreek on a historical date (Open-Meteo archive)."""
    df = fetch_millcreek_range(date, date)
    if not df.empty:
        df["date"] = date
    return df


def fetch_millcreek_forecast(days: int = 2) -> pd.DataFrame:
    """Near-term forecast including tomorrow (Open-Meteo forecast API)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": MILLCREEK_LAT,
        "longitude": MILLCREEK_LON,
        "hourly": HOURLY_VARS,
        "forecast_days": max(1, min(days, 7)),
        "timezone": "America/Denver",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return _hourly_frame(r.json().get("hourly") or {})


def load_or_fetch_weather(
    date: str, cache_dir: Path = GRID_CACHE_DIR
) -> pd.DataFrame:
    path = cache_dir / date / "weather.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        df = pd.read_parquet(path)
        # Upgrade old caches missing precip/snow
        if "precip_mm" in df.columns and "snowfall_cm" in df.columns:
            return df
    df = fetch_millcreek_day(date)
    df.to_parquet(path, index=False)
    return df
