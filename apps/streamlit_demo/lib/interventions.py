from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .paths import IMPLIED_STOCK, SCALE
from .flex_shift import N_COHORTS, SFH_TARGET_PEAK_FRAC
from .schedule_opt import (
    APT_TARGET_PEAK_FRAC,
    SFH_TARGET_PEAK_FRAC as OPT_SFH_PEAK,
    solve_flex_or_fallback,
)

KW_PER_HOME = 1.2  # HB 340 AC cap
PERF_RATIO = 0.85
# ~2 kWh avg surplus / up to ~4–5 kWh summer harvest per research
BATTERY_KWH_PER_HOME = 5.0
SFH_SHARE = 0.75
APT_SHARE = 0.25
JAM_HOURS = set(range(18, 23))
CHARGE_HOURS = set(range(10, 17))
MORNING_DISCHARGE = set(range(3, 6))  # HE3–5 night trickle
# ~85% jam (TOU peak) / ~15% HE3–5 non-peak baseline
MORNING_SOC_FRAC = 0.15
# Midday homes are often empty — only this fraction of adopting share is on-site
# (calibrated so surplus ≈ ~2 kWh/home·day of ~5.3 kWh gen; summer higher)
DAYTIME_COINCIDENCE = 0.38

APPLIANCE_FLEX_COLS = (
    "wasatch_flex_hvac_mw",
    "wasatch_flex_laundry_mw",
    "wasatch_flex_dishwasher_mw",
    "wasatch_flex_ev_mw",
)
FLEX_COLS = (*APPLIANCE_FLEX_COLS, "wasatch_flexible_mw")


@dataclass
class ReliefResult:
    hourly: pd.DataFrame
    metrics: dict


def plug_in_solar_mw(
    weather: pd.DataFrame,
    adoption_pct: float,
    *,
    n_homes: float | None = None,
    kw_per_home: float = KW_PER_HOME,
) -> pd.Series:
    """Plug-in solar MW for a home count (default full stock; callers pass SFH-only)."""
    homes = float(n_homes if n_homes is not None else IMPLIED_STOCK)
    adopting = homes * (adoption_pct / 100.0)
    sw = (
        weather.set_index("hour_ending")["shortwave_wm2"]
        .reindex(range(1, 25))
        .fillna(0.0)
    )
    per_home_kw = kw_per_home * (sw / 1000.0) * PERF_RATIO
    return (adopting * per_home_kw) / 1000.0


def apply_battery(
    net_mw: np.ndarray,
    solar_mw: np.ndarray,
    hours: np.ndarray,
    adoption_pct: float,
    *,
    n_homes: float,
    battery_kwh: float = BATTERY_KWH_PER_HOME,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float, float, float]:
    """
    HB 340: serve adopting-home load live; store surplus only; discharge same MWh.
    charge_mw.sum() == discharge_mw.sum() (yellow == purple).
    Returns net, solar_to_load, charge, discharge, jam_mwh, he35_mwh, surplus_mwh, live_mwh.
    """
    adopt_frac = max(float(adoption_pct), 0.0) / 100.0
    adopting = n_homes * adopt_frac
    capacity_mwh = adopting * battery_kwh / 1000.0
    charge_mw = np.zeros_like(net_mw, dtype=float)
    solar_to_load = np.zeros_like(net_mw, dtype=float)
    surplus_raw = np.zeros_like(net_mw, dtype=float)
    soc = 0.0

    # Daytime: live offset first; only excess charges the battery.
    # Midday coincidence < 1 models empty-house baseload (research ~3.3 live / ~2.0 surplus).
    for i, h in enumerate(hours):
        gen = float(solar_mw[i])
        if gen <= 1e-12:
            continue
        coin = DAYTIME_COINCIDENCE if h in CHARGE_HOURS else 1.0
        adopting_load = max(float(net_mw[i]) * adopt_frac * coin, 0.0)
        live = min(gen, adopting_load)
        surplus = max(0.0, gen - live)
        surplus_raw[i] = surplus
        if h in CHARGE_HOURS and surplus > 1e-9 and soc < capacity_mwh - 1e-9:
            take = min(surplus, capacity_mwh - soc)
            if take > 1e-9:
                charge_mw[i] = take
                soc += take
        solar_to_load[i] = live

    charged = float(charge_mw.sum())
    live_mwh = float(solar_to_load.sum())
    surplus_mwh = float(surplus_raw.sum())
    discharge = np.zeros_like(net_mw, dtype=float)
    jam_idx = [i for i, h in enumerate(hours) if h in JAM_HOURS]
    morn_idx = [i for i, h in enumerate(hours) if h in MORNING_DISCHARGE]

    if charged > 1e-9 and (jam_idx or morn_idx):
        jam_budget = charged * (1.0 - MORNING_SOC_FRAC)
        morn_budget = charged - jam_budget
        pre = np.maximum(net_mw - solar_to_load, 0.0)

        def _weights(indices: list[int]) -> np.ndarray:
            w = np.array([max(float(pre[i]), 1e-6) for i in indices], dtype=float)
            return w / w.sum()

        if jam_idx and jam_budget > 0:
            for i, w in zip(jam_idx, _weights(jam_idx)):
                discharge[i] += jam_budget * float(w)
        if morn_idx and morn_budget > 0:
            for i, w in zip(morn_idx, _weights(morn_idx)):
                discharge[i] += morn_budget * float(w)
        discharge *= charged / max(float(discharge.sum()), 1e-12)

    out = np.maximum(net_mw - solar_to_load - discharge, 0.0)
    to_jam = float(sum(discharge[i] for i in jam_idx))
    to_morning = float(sum(discharge[i] for i in morn_idx))
    return (
        out,
        solar_to_load,
        charge_mw,
        discharge,
        to_jam,
        to_morning,
        surplus_mwh,
        live_mwh,
    )


def _slice_frame(df: pd.DataFrame, share: float) -> pd.DataFrame:
    out = df.copy()
    out["wasatch_mw"] = out["wasatch_mw"].to_numpy(dtype=float) * share
    for col in FLEX_COLS:
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype=float) * share
        else:
            out[col] = 0.0
    return out


def _ensure_evening_flex(frame: pd.DataFrame, flex_frac: float = 0.16) -> pd.DataFrame:
    """If flex columns are empty, synthesize modest evening flex (avoid V-needles)."""
    out = frame.copy()
    cols = [c for c in APPLIANCE_FLEX_COLS if c in out.columns]
    total_flex = sum(float(out[c].sum()) for c in cols)
    if total_flex > 1.0:
        he = out["hour_ending"].to_numpy()
        base = out["wasatch_mw"].to_numpy(dtype=float)
        jam = (he >= 18) & (he <= 22)
        stack = np.zeros(len(out), dtype=float)
        for col in cols:
            stack += out[col].to_numpy(dtype=float)
        cap = np.where(jam, base * flex_frac, stack)
        scale = np.ones(len(out), dtype=float)
        over = stack > cap + 1e-9
        scale[over] = cap[over] / stack[over]
        for col in cols:
            out[col] = out[col].to_numpy(dtype=float) * scale
        out["wasatch_flexible_mw"] = sum(
            out[c].to_numpy(dtype=float) for c in cols
        )
        return out
    he = out["hour_ending"].to_numpy()
    base = out["wasatch_mw"].to_numpy(dtype=float)
    mask = (he >= 18) & (he <= 22)
    flex = np.where(mask, base * flex_frac, 0.0)
    out["wasatch_flex_hvac_mw"] = flex * 0.40
    out["wasatch_flex_ev_mw"] = flex * 0.30
    out["wasatch_flex_laundry_mw"] = flex * 0.15
    out["wasatch_flex_dishwasher_mw"] = flex * 0.15
    out["wasatch_flexible_mw"] = flex
    return out


def _rush_hours(scored: pd.DataFrame) -> set[int]:
    if not scored.empty and "is_jam" in scored.columns:
        jam = set(
            scored.loc[scored["is_jam"], "hour_ending"].astype(int).tolist()
        )
        if jam:
            return jam
    return set(JAM_HOURS)


def apply_relief_stack(
    demand: pd.DataFrame,
    weather: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    adoption_pct: float = 0.0,
    use_battery: bool = False,
    use_flex: bool = True,
) -> ReliefResult:
    """
    Split 75% SFH / 25% apt.
    Order: flex stagger → SFH solar offsets grid → battery (jam-heavy, HE3–5 slice).
    Apt: flex only (no solar/battery). Flex is independent of solar adoption.
    """
    df = demand.sort_values("hour_ending").reset_index(drop=True).copy()
    hours = df["hour_ending"].to_numpy()
    baseline = df["wasatch_mw"].to_numpy(dtype=float).copy()
    do_flex = bool(use_flex)
    do_solar = float(adoption_pct) > 0
    do_battery = bool(use_battery) and do_solar
    rush = _rush_hours(scored)

    if not do_flex and not do_solar:
        hourly = pd.DataFrame(
            {
                "hour_ending": hours,
                "wasatch_mw_baseline": baseline,
                "wasatch_mw_before": baseline,
                "wasatch_mw_after": baseline.copy(),
                "wasatch_mw_after_der": baseline.copy(),
                "sfh_mw_after": baseline * SFH_SHARE,
                "apt_mw_after": baseline * APT_SHARE,
                "solar_mw": np.zeros_like(baseline),
                "solar_to_load_mw": np.zeros_like(baseline),
                "battery_charge_mw": np.zeros_like(baseline),
                "battery_discharge_mw": np.zeros_like(baseline),
            }
        )
        if "date" in df.columns:
            hourly["date"] = df["date"].to_numpy()
        evening = hourly["hour_ending"].isin(list(rush))
        evening_mwh = float(hourly.loc[evening, "wasatch_mw_baseline"].sum())
        peak = float(baseline.max())
        peak_he = int(hours[int(np.argmax(baseline))]) if len(hours) else 0
        return ReliefResult(
            hourly=hourly,
            metrics={
                "peak_mw_before": peak,
                "peak_mw_after": peak,
                "peak_mw_cut": 0.0,
                "after_peak_hour_ending": peak_he,
                "new_peak_created": False,
                "target_peak_mw": peak * SFH_TARGET_PEAK_FRAC,
                "n_cohorts": N_COHORTS,
                "mwh_shifted_total": 0.0,
                "mwh_shifted_by_appliance": {},
                "congestion_avoided": 0.0,
                "co2_kg_before": 0.0,
                "co2_kg_after": 0.0,
                "co2_kg_delta": 0.0,
                "objective_before": 0.0,
                "objective_after": 0.0,
                "objective_delta": 0.0,
                "solver": "identity",
                "opt_formulation": "flex off + adoption 0 → after ≡ baseline",
                "adoption_pct": adoption_pct,
                "use_battery": use_battery,
                "use_flex": False,
                "sfh_share": SFH_SHARE,
                "apt_share": APT_SHARE,
                "solar_mwh": 0.0,
                "solar_mwh_sfh_only": 0.0,
                "solar_to_load_mwh": 0.0,
                "solar_live_mwh": 0.0,
                "surplus_mwh": 0.0,
                "battery_mwh_charged": 0.0,
                "battery_mwh_discharged": 0.0,
                "battery_to_he3_5_mwh": 0.0,
                "battery_to_jam_mwh": 0.0,
                "battery_kwh_per_home_day": 0.0,
                "evening_mwh_before": evening_mwh,
                "evening_mwh_after": evening_mwh,
                "evening_mwh_relieved": 0.0,
                "headline_mwh_off_rush": 0.0,
                "scale_homes": SCALE,
                "implied_stock": IMPLIED_STOCK,
                "kw_per_home": KW_PER_HOME,
                "battery_kwh_per_home": 0.0,
            },
        )

    wx = (
        weather.sort_values("hour_ending")
        if not weather.empty
        else pd.DataFrame(
            {"hour_ending": list(range(1, 25)), "shortwave_wm2": [0.0] * 24}
        )
    )

    sfh_homes = IMPLIED_STOCK * SFH_SHARE
    sfh = _ensure_evening_flex(_slice_frame(df, SFH_SHARE), flex_frac=0.24)
    apt = _ensure_evening_flex(_slice_frame(df, APT_SHARE), flex_frac=0.14)

    sfh_base = sfh["wasatch_mw"].to_numpy(dtype=float).copy()
    apt_base = apt["wasatch_mw"].to_numpy(dtype=float).copy()

    # --- 1) Constrained flex opt first (min congestion·load s.t. caps) ---
    flex_sfh: dict = {
        "mwh_shifted_total": 0.0,
        "mwh_shifted_by_appliance": {},
        "congestion_avoided": 0.0,
        "co2_kg_delta": 0.0,
        "co2_kg_before": 0.0,
        "co2_kg_after": 0.0,
        "objective_before": 0.0,
        "objective_after": 0.0,
        "n_cohorts": N_COHORTS,
        "solver": "skipped",
    }
    sfh_flexed = sfh_base.copy()
    if do_flex and not scored.empty:
        opt_s = solve_flex_or_fallback(
            sfh,
            scored,
            target_peak_frac=OPT_SFH_PEAK,
            path_label="sfh",
        )
        sfh_flexed = opt_s.hourly["wasatch_mw_after"].to_numpy(dtype=float)
        flex_sfh = opt_s.metrics

    apt_flexed = apt_base.copy()
    flex_apt: dict = {
        "mwh_shifted_total": 0.0,
        "mwh_shifted_by_appliance": {},
        "congestion_avoided": 0.0,
        "co2_kg_before": 0.0,
        "co2_kg_after": 0.0,
        "co2_kg_delta": 0.0,
        "objective_before": 0.0,
        "objective_after": 0.0,
        "solver": "skipped",
    }
    if do_flex and not scored.empty:
        opt_a = solve_flex_or_fallback(
            apt,
            scored,
            target_peak_frac=APT_TARGET_PEAK_FRAC,
            path_label="apt",
        )
        apt_flexed = opt_a.hourly["wasatch_mw_after"].to_numpy(dtype=float)
        flex_apt = opt_a.metrics

    # --- 2) SFH solar: adopting homes serve load from the panel, not the grid ---
    solar = np.zeros_like(sfh_flexed)
    discharge = np.zeros_like(sfh_flexed)
    charge_mw = np.zeros_like(sfh_flexed)
    adopt_frac = max(float(adoption_pct), 0.0) / 100.0
    solar_to_load = np.zeros_like(sfh_flexed)
    surplus_mwh = 0.0
    live_mwh = 0.0
    batt_jam = 0.0
    batt_morning = 0.0

    if do_solar:
        solar = (
            plug_in_solar_mw(wx, adoption_pct, n_homes=sfh_homes)
            .reindex(hours)
            .fillna(0.0)
            .to_numpy()
        )
        for i, h in enumerate(hours):
            gen = float(solar[i])
            if gen <= 1e-12:
                continue
            coin = DAYTIME_COINCIDENCE if int(h) in CHARGE_HOURS else 1.0
            adopting_load = max(float(sfh_flexed[i]) * adopt_frac * coin, 0.0)
            solar_to_load[i] = min(gen, adopting_load)
        surplus_mwh = float(np.maximum(solar - solar_to_load, 0.0).sum())
        live_mwh = float(solar_to_load.sum())

    if do_battery:
        (
            sfh_after,
            solar_to_load,
            charge_mw,
            discharge,
            batt_jam,
            batt_morning,
            surplus_mwh,
            live_mwh,
        ) = apply_battery(
            sfh_flexed, solar, hours, adoption_pct, n_homes=sfh_homes
        )
    else:
        sfh_after = np.maximum(sfh_flexed - solar_to_load, 0.0)

    apt_after = apt_flexed
    after = sfh_after + apt_after
    before_peak = float(baseline.max())
    after_peak = float(after.max())
    after_peak_he = int(hours[int(np.argmax(after))])
    jam_hours = rush

    hourly = pd.DataFrame(
        {
            "hour_ending": hours,
            "wasatch_mw_baseline": baseline,
            "wasatch_mw_before": baseline,
            "wasatch_mw_after": after,
            "wasatch_mw_after_der": after,
            "sfh_mw_after": sfh_after,
            "apt_mw_after": apt_after,
            "solar_mw": solar,
            "solar_to_load_mw": solar_to_load,
            "battery_charge_mw": charge_mw,
            "battery_discharge_mw": discharge,
        }
    )
    if "date" in df.columns:
        hourly["date"] = df["date"].to_numpy()

    evening = hourly["hour_ending"].isin(list(rush))
    evening_before = float(hourly.loc[evening, "wasatch_mw_baseline"].sum())
    evening_after = float(hourly.loc[evening, "wasatch_mw_after"].sum())

    moved_by = dict(flex_sfh.get("mwh_shifted_by_appliance") or {})
    for k, v in (flex_apt.get("mwh_shifted_by_appliance") or {}).items():
        moved_by[k] = moved_by.get(k, 0.0) + float(v)

    metrics = {
        "peak_mw_before": before_peak,
        "peak_mw_after": after_peak,
        "peak_mw_cut": before_peak - after_peak,
        "after_peak_hour_ending": after_peak_he,
        "new_peak_created": bool(
            after_peak_he not in jam_hours and after_peak > before_peak * 0.85
        ),
        "target_peak_mw": before_peak * SFH_TARGET_PEAK_FRAC,
        "n_cohorts": N_COHORTS,
        "mwh_shifted_total": float(flex_sfh.get("mwh_shifted_total", 0))
        + float(flex_apt.get("mwh_shifted_total", 0)),
        "mwh_shifted_by_appliance": moved_by,
        "congestion_avoided": float(flex_sfh.get("congestion_avoided", 0))
        + float(flex_apt.get("congestion_avoided", 0)),
        "co2_kg_before": float(flex_sfh.get("co2_kg_before", 0))
        + float(flex_apt.get("co2_kg_before", 0)),
        "co2_kg_after": float(flex_sfh.get("co2_kg_after", 0))
        + float(flex_apt.get("co2_kg_after", 0)),
        "co2_kg_delta": float(flex_sfh.get("co2_kg_delta", 0))
        + float(flex_apt.get("co2_kg_delta", 0)),
        "objective_before": float(flex_sfh.get("objective_before", 0))
        + float(flex_apt.get("objective_before", 0)),
        "objective_after": float(flex_sfh.get("objective_after", 0))
        + float(flex_apt.get("objective_after", 0)),
        "objective_delta": (
            float(flex_sfh.get("objective_before", 0))
            + float(flex_apt.get("objective_before", 0))
            - float(flex_sfh.get("objective_after", 0))
            - float(flex_apt.get("objective_after", 0))
        ),
        "solver": flex_sfh.get("solver") or flex_apt.get("solver") or "skipped",
        "opt_formulation": (
            "min Σ (c_h + λ·CI_h)·load_h  s.t. energy balance, "
            "flex budgets, peak cap, appliance windows"
        ),
        "adoption_pct": adoption_pct,
        "use_battery": use_battery,
        "use_flex": do_flex,
        "sfh_share": SFH_SHARE,
        "apt_share": APT_SHARE,
        "solar_mwh": float(solar.sum()),
        "solar_mwh_sfh_only": float(solar.sum()),
        "solar_to_load_mwh": float(np.asarray(solar_to_load).sum()),
        "solar_live_mwh": float(live_mwh),
        "surplus_mwh": float(surplus_mwh),
        "battery_mwh_charged": float(charge_mw.sum()),
        "battery_mwh_discharged": float(discharge.sum()),
        "battery_to_he3_5_mwh": float(batt_morning),
        "battery_to_jam_mwh": float(batt_jam),
        "battery_kwh_per_home_day": (
            float(charge_mw.sum()) * 1000.0 / max(sfh_homes * adopt_frac, 1.0)
            if use_battery and adopt_frac > 0
            else 0.0
        ),
        "evening_mwh_before": evening_before,
        "evening_mwh_after": evening_after,
        "evening_mwh_relieved": evening_before - evening_after,
        "headline_mwh_off_rush": evening_before - evening_after,
        "scale_homes": SCALE,
        "implied_stock": IMPLIED_STOCK,
        "kw_per_home": KW_PER_HOME,
        "battery_kwh_per_home": BATTERY_KWH_PER_HOME if use_battery else 0.0,
    }
    return ReliefResult(hourly=hourly, metrics=metrics)
