from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .paths import (
    EFFECTIVE_MULT,
    HOUSING_LATEST,
    IMPLIED_STOCK,
    INTENSITY,
    N_SAMPLE_HOMES,
    SCALE,
    WASATCH_COUNTIES,
    WASATCH_DAY_DIR,
)

SUM_COLS = [
    "kwh",
    "flexible_kwh",
    "flex_hvac_kwh",
    "flex_laundry_kwh",
    "flex_dishwasher_kwh",
    "flex_ev_kwh",
]


def list_housing_parts(housing_dir: Path = HOUSING_LATEST) -> list[Path]:
    parts = sorted(housing_dir.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"No part-*.parquet under {housing_dir}")
    return parts


def aggregate_fleet_hourly(
    housing_dir: Path = HOUSING_LATEST,
    dates: list[str] | None = None,
) -> pd.DataFrame:
    """Sum 5k-home sample by date × hour across all parquet parts."""
    cols = ["date", "hour_ending", "hour_label", *SUM_COLS]
    chunks: list[pd.DataFrame] = []
    for path in list_housing_parts(housing_dir):
        df = pd.read_parquet(path, columns=cols)
        if dates is not None:
            df = df[df["date"].isin(dates)]
        if df.empty:
            continue
        g = df.groupby(["date", "hour_ending", "hour_label"], as_index=False)[
            SUM_COLS
        ].sum()
        chunks.append(g)
    if not chunks:
        raise ValueError("No rows to aggregate for requested dates")
    fleet = (
        pd.concat(chunks, ignore_index=True)
        .groupby(["date", "hour_ending", "hour_label"], as_index=False)[SUM_COLS]
        .sum()
        .sort_values(["date", "hour_ending"])
        .reset_index(drop=True)
    )
    return fleet


def scale_to_wasatch(
    fleet: pd.DataFrame,
    scale: int = SCALE,
    intensity: float = INTENSITY,
) -> pd.DataFrame:
    """Stock ×scale and intensity×, then kWh → MW for 1h intervals."""
    mult = float(scale) * float(intensity)
    out = fleet.copy()
    for col in SUM_COLS:
        out[f"fleet_{col}"] = out[col]
        out[f"wasatch_{col}"] = out[col] * mult
        out[f"wasatch_{col.replace('kwh', 'mw')}"] = out[f"wasatch_{col}"] / 1000.0
    out["fleet_mw"] = out["fleet_kwh"] / 1000.0
    out["wasatch_mw"] = out["wasatch_kwh"] / 1000.0
    out["wasatch_flexible_mw"] = out["wasatch_flexible_kwh"] / 1000.0
    out["scale"] = scale
    out["intensity"] = float(intensity)
    out["effective_mult"] = mult
    return out


def available_dates(
    housing_dir: Path = HOUSING_LATEST,
    wasatch_dir: Path = WASATCH_DAY_DIR,
) -> list[str]:
    all_path = wasatch_dir / "all_days.parquet"
    if all_path.is_file():
        df = pd.read_parquet(all_path, columns=["date"])
        return sorted(str(d) for d in df["date"].unique().tolist())
    dates: set[str] = set()
    for path in list_housing_parts(housing_dir):
        df = pd.read_parquet(path, columns=["date"])
        dates.update(str(d) for d in df["date"].unique().tolist())
    return sorted(dates)


def write_wasatch_days(
    scaled: pd.DataFrame,
    out_dir: Path = WASATCH_DAY_DIR,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_path = out_dir / "all_days.parquet"
    scaled.to_parquet(all_path, index=False)

    by_date = {d: g for d, g in scaled.groupby("date")}
    for day, frame in by_date.items():
        day_dir = out_dir / str(day)
        day_dir.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(day_dir / "demand.parquet", index=False)

    counties = " + ".join(WASATCH_COUNTIES)
    meta = {
        "created_utc": datetime.utcnow().isoformat() + "Z",
        "n_sample_homes": N_SAMPLE_HOMES,
        "scale": SCALE,
        "intensity": INTENSITY,
        "effective_mult": EFFECTIVE_MULT,
        "implied_stock": IMPLIED_STOCK,
        "counties": list(WASATCH_COUNTIES),
        "n_days": int(scaled["date"].nunique()),
        "day_start": str(scaled["date"].min()),
        "day_end": str(scaled["date"].max()),
        "mw_note": (
            "MW = kWh/1000 for 1h bins; "
            "wasatch_mw = sum(fleet_kwh)*scale*intensity/1000"
        ),
        "provenance": (
            f"5k RMP-seeded homes × stock {SCALE} ≈ {IMPLIED_STOCK:,} "
            f"households ({counties}) × intensity {INTENSITY} "
            f"(light Millcreek seed → ~10k kWh/home·year target)"
        ),
        "all_days_parquet": str(all_path),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return out_dir


def load_wasatch_day(date: str, out_dir: Path = WASATCH_DAY_DIR) -> pd.DataFrame:
    path = out_dir / date / "demand.parquet"
    if path.is_file():
        return pd.read_parquet(path)
    all_path = out_dir / "all_days.parquet"
    if all_path.is_file():
        df = pd.read_parquet(all_path)
        return df[df["date"] == date].copy()
    raise FileNotFoundError(f"No Wasatch day artifact for {date} under {out_dir}")
