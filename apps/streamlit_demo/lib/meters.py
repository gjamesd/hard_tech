from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .paths import RAW_HOURLY_DIR

RAW_HOURLY = RAW_HOURLY_DIR
SEED_SFH = "342856918_sfh"
SEED_CASITA = "50752799_casita"
HOURS = [f"{h:02d}:00" for h in range(1, 25)]


def _load_day_vec(seed_key: str, date: str) -> np.ndarray | None:
    path = RAW_HOURLY / seed_key / f"{date}.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text())
    body = payload.get("getIntervalUsageForDateResponseBody") or {}
    rows = (body.get("response") or {}).get("intervalDataResponse") or []
    if not isinstance(rows, list) or len(rows) < 20:
        return None
    by_hour = {str(r.get("readTime")): float(r.get("usage") or 0.0) for r in rows}
    vec = np.array([by_hour.get(h, 0.0) for h in HOURS], dtype=np.float64)
    if vec.sum() <= 0:
        return None
    return vec


def _flex_weights(kind: str) -> np.ndarray:
    m = np.zeros(24, dtype=np.float64)
    if kind == "sfh":
        m[17:23] = 1.0
        m[10:14] = 0.5
        frac = 0.35
    else:
        m[18:23] = 1.0
        m[9:12] = 0.4
        frac = 0.17
    if m.sum() > 0:
        m = m / m.sum()
    return m * frac * 24.0


def meter_day_frame(seed_key: str, label: str, kind: str, date: str) -> pd.DataFrame:
    vec = _load_day_vec(seed_key, date)
    if vec is None:
        return pd.DataFrame()
    weights = _flex_weights(kind)
    flexible = np.minimum(vec * weights, vec * 0.95)
    baseload = np.maximum(vec - flexible, 0.0)
    return pd.DataFrame(
        {
            "meter": label,
            "seed_key": seed_key,
            "date": date,
            "hour_ending": list(range(1, 25)),
            "kwh": vec,
            "flexible_kwh": flexible,
            "baseload_kwh": baseload,
        }
    )


def available_meter_dates(seed_key: str = SEED_SFH) -> list[str]:
    folder = RAW_HOURLY / seed_key
    if not folder.is_dir():
        return []
    out: list[str] = []
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            out.append(path.stem)
        except ValueError:
            continue
    return out


def latest_shared_meter_date(on_or_before: str | None = None) -> str | None:
    sfh = set(available_meter_dates(SEED_SFH))
    casita = set(available_meter_dates(SEED_CASITA))
    shared = sorted(sfh & casita)
    if on_or_before is not None:
        shared = [d for d in shared if d <= on_or_before]
    return shared[-1] if shared else None


def load_house_casita(date: str) -> dict[str, pd.DataFrame]:
    """Brief demo step 2 — real RMP main house + casita with flex vs baseload."""
    sfh = meter_day_frame(SEED_SFH, "Main house", "sfh", date)
    casita = meter_day_frame(SEED_CASITA, "Casita", "apartment", date)
    return {"main_house": sfh, "casita": casita, "meter_date": date}


def load_house_casita_best(date: str) -> dict[str, pd.DataFrame | str | bool]:
    """
    Load meters for date; if missing, fall back to latest shared Green Button day
    on or before date.
    """
    meters = load_house_casita(date)
    if not meters["main_house"].empty and not meters["casita"].empty:
        return {**meters, "is_proxy": False}
    fallback = latest_shared_meter_date(on_or_before=date)
    if not fallback:
        return {**meters, "is_proxy": False}
    meters = load_house_casita(fallback)
    return {**meters, "is_proxy": True, "meter_date": fallback}


def propose_house_shift(
    frame: pd.DataFrame, jam_hours: set[int] | None = None
) -> pd.DataFrame:
    """Simple agent: move flexible kWh out of jam hours into lowest-load valley hours."""
    if frame.empty:
        return frame
    jam_hours = jam_hours or set(range(18, 23))
    out = frame.copy()
    kwh = out["kwh"].to_numpy(dtype=float).copy()
    flex = out["flexible_kwh"].to_numpy(dtype=float).copy()
    hours = out["hour_ending"].to_numpy()
    donors = [i for i, h in enumerate(hours) if h in jam_hours and flex[i] > 1e-6]
    receivers = [i for i, h in enumerate(hours) if h not in jam_hours]
    receivers.sort(key=lambda i: kwh[i])
    n_recv = max(len(receivers), 1)
    for di in donors:
        rem = float(flex[di])
        if rem <= 1e-6 or not receivers:
            continue
        share = rem / n_recv
        for ri in receivers:
            if rem <= 1e-6:
                break
            take = min(rem, share)
            kwh[di] -= take
            flex[di] -= take
            kwh[ri] += take
            flex[ri] += take
            rem -= take
        if rem > 1e-6:
            kwh[receivers[0]] += rem
            flex[receivers[0]] += rem
            kwh[di] -= rem
            flex[di] -= rem
    out["kwh_after"] = kwh
    out["flexible_kwh_after"] = flex
    out["baseload_kwh_after"] = np.maximum(kwh - flex, 0.0)
    return out
