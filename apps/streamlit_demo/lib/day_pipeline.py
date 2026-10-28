from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .aggregate import load_wasatch_day
from .congestion import SCORE_DOC, congestion_score_v0
from .eia_pace import load_or_fetch_day
from .flex_shift import appliance_aware_shift
from .open_meteo import load_or_fetch_weather
from .paths import DEMO_PACK_DIR, WASATCH_DAY_DIR


def build_day_pack(
    date: str,
    *,
    wasatch_dir: Path = WASATCH_DAY_DIR,
    out_dir: Path = DEMO_PACK_DIR,
    fetch_grid: bool = True,
) -> Path:
    """Assemble demand + grid + score + shift for one date into demo_day_pack/<date>/."""
    demand = load_wasatch_day(date, wasatch_dir)
    pack = out_dir / date
    pack.mkdir(parents=True, exist_ok=True)

    demand.to_parquet(pack / "demand.parquet", index=False)

    grid_ok = False
    demand_ba = pd.DataFrame()
    fuel = pd.DataFrame()
    weather = pd.DataFrame()
    error = None

    grid_source = None
    if fetch_grid:
        try:
            grid = load_or_fetch_day(date)
            demand_ba = grid["demand"]
            fuel = grid["fuel"]
            grid_source = grid.get("source", "eia")
            weather = load_or_fetch_weather(date)
            grid_ok = True
        except Exception as exc:  # noqa: BLE001 — hackathon: surface in meta
            error = str(exc)

    if not demand_ba.empty:
        demand_ba.to_parquet(pack / "pace_demand.parquet", index=False)
    if not fuel.empty:
        fuel.to_parquet(pack / "pace_fuel.parquet", index=False)
    if not weather.empty:
        weather.to_parquet(pack / "weather.parquet", index=False)

    scored = pd.DataFrame()
    shift_hourly = pd.DataFrame()
    metrics: dict = {}
    if grid_ok and not demand_ba.empty:
        scored = congestion_score_v0(demand_ba, fuel if not fuel.empty else None)
        scored.to_parquet(pack / "congestion.parquet", index=False)
        result = appliance_aware_shift(demand, scored)
        shift_hourly = result.hourly
        metrics = result.metrics
        shift_hourly.to_parquet(pack / "shift.parquet", index=False)
        (pack / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    (pack / "congestion_score_v0.txt").write_text(SCORE_DOC)
    meta = {
        "date": date,
        "created_utc": datetime.utcnow().isoformat() + "Z",
        "grid_ok": grid_ok,
        "grid_source": grid_source,
        "error": error,
        "has_shift": not shift_hourly.empty,
        "metrics": metrics,
    }
    (pack / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return pack


def load_day_pack(date: str, out_dir: Path = DEMO_PACK_DIR) -> dict:
    pack = out_dir / date
    if not pack.is_dir():
        raise FileNotFoundError(pack)
    out: dict = {"date": date, "dir": pack}
    for name in (
        "demand",
        "pace_demand",
        "pace_fuel",
        "weather",
        "congestion",
        "shift",
    ):
        path = pack / f"{name}.parquet"
        out[name] = pd.read_parquet(path) if path.is_file() else pd.DataFrame()
    meta_path = pack / "meta.json"
    out["meta"] = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    metrics_path = pack / "metrics.json"
    out["metrics"] = (
        json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    )
    return out
