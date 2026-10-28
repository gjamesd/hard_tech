from __future__ import annotations

import json
from datetime import date as date_cls
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error

from .open_meteo import (
    fetch_millcreek_forecast,
    fetch_millcreek_range,
    load_or_fetch_weather,
)
from .paths import (
    GRID_CACHE_DIR,
    MODEL_META_PATH,
    MODELS_DIR,
    PACE_MODEL_PATH,
    WASATCH_DAY_DIR,
    WASATCH_MODEL_PATH,
)
from .eia_pace import synthetic_pace_day

FEATURE_COLS = [
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_6",
    "lag_12",
    "lag_24",
    "lag_168",
    "hour_ending",
    "dow",
    "temp_c",
    "shortwave_wm2",
    "precip_mm",
    "snowfall_cm",
]


def load_wasatch_series() -> pd.DataFrame:
    path = WASATCH_DAY_DIR / "all_days.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_parquet(path, columns=["date", "hour_ending", "wasatch_mw"])
    df = df.sort_values(["date", "hour_ending"]).reset_index(drop=True)
    df["target"] = df["wasatch_mw"]
    df["series"] = "wasatch"
    return df


def load_pace_series() -> pd.DataFrame:
    rows = []
    have: set[str] = set()
    if GRID_CACHE_DIR.is_dir():
        for d in sorted(GRID_CACHE_DIR.iterdir()):
            demand_path = d / "demand.parquet"
            if demand_path.is_file():
                part = pd.read_parquet(demand_path)
                if "demand_mw" in part.columns and not part.empty:
                    rows.append(
                        part[["date", "hour_ending", "demand_mw"]].rename(
                            columns={"demand_mw": "target"}
                        )
                    )
                    have.update(part["date"].astype(str).unique().tolist())
    # Fill gaps with labeled synthetic PACE so HGB has a continuous history
    wasatch_days = sorted(load_wasatch_series()["date"].unique())
    for day in wasatch_days:
        if str(day) in have:
            continue
        syn = synthetic_pace_day(str(day))["demand"]
        rows.append(
            syn[["date", "hour_ending", "demand_mw"]].rename(
                columns={"demand_mw": "target"}
            )
        )
    if not rows:
        raise FileNotFoundError("No PACE demand history available")
    df = pd.concat(rows, ignore_index=True)
    df = (
        df.groupby(["date", "hour_ending"], as_index=False)["target"]
        .mean()
        .sort_values(["date", "hour_ending"])
        .reset_index(drop=True)
    )
    df["series"] = "pace"
    return df


def attach_weather(df: pd.DataFrame) -> pd.DataFrame:
    days = sorted(df["date"].unique())
    if not days:
        return df
    # Prefer one range fetch for speed
    try:
        wx = fetch_millcreek_range(days[0], days[-1])
    except Exception:
        chunks = []
        for d in days:
            try:
                chunks.append(load_or_fetch_weather(str(d)))
            except Exception:
                continue
        wx = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if wx.empty:
        df = df.copy()
        df["temp_c"] = 15.0
        df["shortwave_wm2"] = 0.0
        df["precip_mm"] = 0.0
        df["snowfall_cm"] = 0.0
        return df
    wx = wx.drop_duplicates(["date", "hour_ending"])
    out = df.merge(wx, on=["date", "hour_ending"], how="left")
    for col, fill in (
        ("temp_c", 15.0),
        ("shortwave_wm2", 0.0),
        ("precip_mm", 0.0),
        ("snowfall_cm", 0.0),
    ):
        out[col] = out[col].fillna(fill)
    return out


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["date", "hour_ending"]).reset_index(drop=True).copy()
    y = out["target"]
    out["lag_1"] = y.shift(1)
    out["lag_2"] = y.shift(2)
    out["lag_3"] = y.shift(3)
    out["lag_6"] = y.shift(6)
    out["lag_12"] = y.shift(12)
    out["lag_24"] = y.shift(24)
    out["lag_168"] = y.shift(168)
    out["dow"] = pd.to_datetime(out["date"]).dt.dayofweek
    return out


def train_one(df: pd.DataFrame, name: str) -> tuple[HistGradientBoostingRegressor, dict]:
    framed = add_lag_features(attach_weather(df)).dropna(subset=FEATURE_COLS + ["target"])
    days = sorted(framed["date"].unique())
    if len(days) < 10:
        raise ValueError(f"Need more days to train {name}: got {len(days)}")
    holdout = days[-1]
    train = framed[framed["date"] < holdout]
    test = framed[framed["date"] == holdout]
    model = HistGradientBoostingRegressor(
        max_depth=6,
        learning_rate=0.08,
        max_iter=200,
        random_state=42,
    )
    model.fit(train[FEATURE_COLS], train["target"])
    pred = model.predict(test[FEATURE_COLS])
    mae = float(mean_absolute_error(test["target"], pred))
    try:
        mape = float(mean_absolute_percentage_error(test["target"], pred))
    except Exception:
        mape = float("nan")
    meta = {
        "name": name,
        "holdout_date": holdout,
        "n_train_rows": int(len(train)),
        "mae_mw": mae,
        "mape": mape,
        "features": FEATURE_COLS,
    }
    return model, meta


def train_and_save(models_dir: Path = MODELS_DIR) -> dict:
    models_dir.mkdir(parents=True, exist_ok=True)
    wasatch = load_wasatch_series()
    pace = load_pace_series()
    w_model, w_meta = train_one(wasatch, "wasatch")
    p_model, p_meta = train_one(pace, "pace")
    joblib.dump(w_model, WASATCH_MODEL_PATH)
    joblib.dump(p_model, PACE_MODEL_PATH)
    meta = {
        "wasatch": w_meta,
        "pace": p_meta,
        "paths": {
            "wasatch": str(WASATCH_MODEL_PATH),
            "pace": str(PACE_MODEL_PATH),
        },
    }
    MODEL_META_PATH.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def _recursive_predict(
    model: HistGradientBoostingRegressor,
    history: pd.DataFrame,
    weather_day: pd.DataFrame,
    date: str,
) -> pd.DataFrame:
    """Roll forward 24 hours using lags from history + predictions."""
    hist = history.sort_values(["date", "hour_ending"]).copy()
    targets = hist["target"].tolist()
    wx = weather_day.set_index("hour_ending")
    rows = []
    for h in range(1, 25):
        lag_1 = targets[-1]
        lag_2 = targets[-2] if len(targets) > 1 else lag_1
        lag_3 = targets[-3] if len(targets) > 2 else lag_1
        lag_6 = targets[-6] if len(targets) > 5 else lag_1
        lag_12 = targets[-12] if len(targets) > 11 else lag_1
        lag_24 = targets[-24] if len(targets) > 23 else lag_1
        lag_168 = targets[-168] if len(targets) > 167 else lag_24
        if h in wx.index:
            temp = float(wx.loc[h, "temp_c"]) if "temp_c" in wx.columns else 15.0
            sw = float(wx.loc[h, "shortwave_wm2"]) if "shortwave_wm2" in wx.columns else 0.0
            precip = float(wx.loc[h, "precip_mm"]) if "precip_mm" in wx.columns else 0.0
            snow = float(wx.loc[h, "snowfall_cm"]) if "snowfall_cm" in wx.columns else 0.0
        else:
            temp, sw, precip, snow = 15.0, 0.0, 0.0, 0.0
        feats = pd.DataFrame(
            [
                {
                    "lag_1": lag_1,
                    "lag_2": lag_2,
                    "lag_3": lag_3,
                    "lag_6": lag_6,
                    "lag_12": lag_12,
                    "lag_24": lag_24,
                    "lag_168": lag_168,
                    "hour_ending": h,
                    "dow": pd.to_datetime(date).dayofweek,
                    "temp_c": temp,
                    "shortwave_wm2": sw,
                    "precip_mm": precip,
                    "snowfall_cm": snow,
                }
            ]
        )
        yhat = float(model.predict(feats[FEATURE_COLS])[0])
        yhat = max(yhat, 0.0)
        targets.append(yhat)
        rows.append({"date": date, "hour_ending": h, "mw_hat": yhat})
    return pd.DataFrame(rows)


def _weather_for_target(date: str, prefer_forecast_api: bool) -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "hour_ending": list(range(1, 25)),
            "temp_c": [15.0] * 24,
            "shortwave_wm2": [0.0] * 24,
            "precip_mm": [0.0] * 24,
            "snowfall_cm": [0.0] * 24,
            "date": [date] * 24,
        }
    )
    if prefer_forecast_api:
        try:
            wx_all = fetch_millcreek_forecast(days=7)
            wx = wx_all[wx_all["date"] == date]
            if not wx.empty:
                return wx
        except Exception:
            pass
    try:
        return load_or_fetch_weather(date)
    except Exception:
        return empty


def predict_day(
    date: str,
    *,
    use_forecast_weather: bool | None = None,
) -> dict[str, pd.DataFrame | str | int]:
    """
    Predict Wasatch + PACE for calendar day `date` using all history with date < date.
    Example: date=2026-07-28 uses history through 2026-07-27.
    """
    if not WASATCH_MODEL_PATH.is_file() or not PACE_MODEL_PATH.is_file():
        raise FileNotFoundError("Train models first: scripts/rmp/train_demand_forecast.py")
    w_model = joblib.load(WASATCH_MODEL_PATH)
    p_model = joblib.load(PACE_MODEL_PATH)

    wasatch = load_wasatch_series()
    pace = load_pace_series()
    w_hist = wasatch[wasatch["date"] < date]
    p_hist = pace[pace["date"] < date]
    n_hist_days = int(w_hist["date"].nunique()) if not w_hist.empty else 0
    if w_hist.empty:
        raise ValueError(
            f"No Wasatch history before {date} — pick a later day or rebuild aggregates."
        )
    if n_hist_days < 7:
        # Still predict, but caller should surface the warning
        pass
    if p_hist.empty:
        p_hist = w_hist.copy()
        p_hist["target"] = p_hist["target"] * 3.5  # weak fallback; labeled upstream

    if use_forecast_weather is None:
        use_forecast_weather = date >= date_cls.today().isoformat()
    wx = _weather_for_target(date, prefer_forecast_api=bool(use_forecast_weather))

    last_hist = str(w_hist["date"].max())
    return {
        "date": date,
        "history_end": last_hist,
        "n_history_days": n_hist_days,
        "wasatch": _recursive_predict(w_model, w_hist, wx, date),
        "pace": _recursive_predict(p_model, p_hist, wx, date),
        "weather": wx,
    }
