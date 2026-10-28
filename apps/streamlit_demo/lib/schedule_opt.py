from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .flex_shift import (
    APPLIANCES,
    APT_TARGET_PEAK_FRAC,
    N_COHORTS,
    SFH_TARGET_PEAK_FRAC,
    ShiftResult,
    appliance_aware_shift,
)

LAMBDA_CI = 0.05
EPS = 1e-9


@dataclass
class OptResult:
    hourly: pd.DataFrame
    metrics: dict
    solver: str


def _allowed_from_to() -> tuple[set[int], set[int]]:
    frm: set[int] = set()
    to: set[int] = set()
    for _n, _c, f, t in APPLIANCES:
        frm |= f
        to |= t
    return frm, to


def _flex_budget(demand: pd.DataFrame, hours: np.ndarray) -> np.ndarray:
    n = len(hours)
    budget = np.zeros(n, dtype=float)
    for _name, col, _frm, _to in APPLIANCES:
        if col in demand.columns:
            budget += demand[col].to_numpy(dtype=float)
    if float(budget.sum()) <= 1.0 and "wasatch_flexible_mw" in demand.columns:
        budget = demand["wasatch_flexible_mw"].to_numpy(dtype=float)
    return np.maximum(budget, 0.0)


def _normalize_ci(ci: np.ndarray) -> np.ndarray:
    if np.isnan(ci).all():
        return np.zeros_like(ci, dtype=float)
    med = float(np.nanmedian(ci))
    filled = np.where(np.isnan(ci), med, ci).astype(float)
    lo, hi = float(filled.min()), float(filled.max())
    if hi - lo < EPS:
        return np.zeros_like(filled)
    return (filled - lo) / (hi - lo)


def solve_flex_schedule(
    demand: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    target_peak_frac: float = SFH_TARGET_PEAK_FRAC,
    path_label: str = "sfh",
) -> OptResult:
    """
    Minimize congestion-weighted load (+ light CI) subject to:
    energy balance, flex budgets, peak cap, appliance from/to windows.
    Falls back to heuristic if linprog unavailable or fails.
    """
    try:
        from scipy.optimize import linprog
    except ImportError:
        fb = appliance_aware_shift(
            demand, scored, target_peak_frac=target_peak_frac
        )
        fb.metrics["solver"] = "heuristic_fallback"
        fb.metrics["objective_before"] = fb.metrics.get(
            "congestion_weighted_before", 0.0
        )
        fb.metrics["objective_after"] = fb.metrics.get(
            "congestion_weighted_after", 0.0
        )
        return OptResult(hourly=fb.hourly, metrics=fb.metrics, solver="heuristic_fallback")

    df = demand.merge(
        scored[
            [
                c
                for c in (
                    "hour_ending",
                    "congestion_score",
                    "ci_kg_per_mwh",
                    "demand_mw",
                    "is_jam",
                )
                if c in scored.columns
            ]
        ],
        on="hour_ending",
        how="left",
        suffixes=("", "_pace"),
    )
    df = df.sort_values("hour_ending").reset_index(drop=True)
    hours = df["hour_ending"].to_numpy(dtype=int)
    n = len(hours)
    load = df["wasatch_mw"].to_numpy(dtype=float).copy()
    score = df["congestion_score"].fillna(0.5).to_numpy(dtype=float)
    ci_raw = (
        df["ci_kg_per_mwh"].to_numpy(dtype=float)
        if "ci_kg_per_mwh" in df.columns
        else np.full(n, np.nan)
    )
    ci_n = _normalize_ci(ci_raw)
    flex = _flex_budget(df, hours)

    from_h, to_h = _allowed_from_to()
    peak = float(load.max()) if n else 0.0
    p_cap = peak * float(target_peak_frac)

    # Decision vector x = [f_0..f_{n-1}, g_0..g_{n-1}]
    # after_h = L_h - f_h + g_h
    # obj = sum (c_h + λ ci_h) * after_h = const + sum (-c')f + sum (c')g
    cost = score + LAMBDA_CI * ci_n
    c_obj = np.concatenate([-cost, cost])

    bounds: list[tuple[float, float]] = []
    for i, h in enumerate(hours):
        f_max = float(flex[i]) if int(h) in from_h else 0.0
        # Cannot move more than keeps load >= 0
        f_max = min(f_max, max(0.0, float(load[i])))
        bounds.append((0.0, f_max))
    for i, h in enumerate(hours):
        if int(h) not in to_h:
            bounds.append((0.0, 0.0))
            continue
        room = max(0.0, p_cap - float(load[i]))
        bounds.append((0.0, room))

    # Equality: sum f - sum g = 0
    a_eq = np.concatenate([np.ones(n), -np.ones(n)])[None, :]
    b_eq = np.array([0.0])

    jam_hours: set[int] = set()
    if "is_jam" in df.columns:
        jam_hours = set(
            df.loc[df["is_jam"].fillna(False), "hour_ending"].astype(int).tolist()
        )
    if not jam_hours:
        jam_hours = set(range(18, 23))

    # Peak cap on non-jam hours only (avoid infeasible "shave jam below cap
    # without enough flex"). Receivers already bounded by g <= p_cap - L.
    a_ub_list = []
    b_ub_list = []
    for i, h in enumerate(hours):
        if int(h) in jam_hours:
            continue
        # L - f + g <= p_cap
        row = np.zeros(2 * n)
        row[i] = -1.0
        row[n + i] = 1.0
        a_ub_list.append(row)
        b_ub_list.append(p_cap - float(load[i]))

    a_ub = np.vstack(a_ub_list) if a_ub_list else None
    b_ub = np.asarray(b_ub_list, dtype=float) if b_ub_list else None

    result = linprog(
        c_obj,
        A_ub=a_ub,
        b_ub=b_ub,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        fb = appliance_aware_shift(
            demand, scored, target_peak_frac=target_peak_frac
        )
        fb.metrics["solver"] = "heuristic_fallback"
        fb.metrics["linprog_message"] = str(result.message)
        fb.metrics["objective_before"] = float(np.dot(load, cost))
        fb.metrics["objective_after"] = fb.metrics.get(
            "congestion_weighted_after", 0.0
        )
        return OptResult(
            hourly=fb.hourly, metrics=fb.metrics, solver="heuristic_fallback"
        )

    x = result.x
    f = x[:n]
    g = x[n:]
    after = np.maximum(load - f + g, 0.0)
    moved = float(f.sum())

    obj_before = float(np.dot(load, cost))
    obj_after = float(np.dot(after, cost))
    before_w = float(np.dot(load, score))
    after_w = float(np.dot(after, score))
    before_co2 = float(np.dot(load, np.where(np.isnan(ci_raw), 0.0, ci_raw)))
    after_co2 = float(np.dot(after, np.where(np.isnan(ci_raw), 0.0, ci_raw)))

    after_peak = float(after.max()) if n else 0.0
    after_peak_he = int(hours[int(np.argmax(after))]) if n else 0
    new_peak = after_peak_he not in jam_hours and after_peak > peak * 0.85

    out = df.copy()
    out["wasatch_mw_before"] = load
    out["wasatch_mw_after"] = after
    out["flex_out_mw"] = f
    out["flex_in_mw"] = g

    metrics = {
        "peak_mw_before": peak,
        "peak_mw_after": after_peak,
        "peak_mw_cut": peak - after_peak,
        "after_peak_hour_ending": after_peak_he,
        "new_peak_created": bool(new_peak),
        "target_peak_mw": p_cap,
        "n_cohorts": N_COHORTS,
        "mwh_shifted_total": moved,
        "mwh_shifted_by_appliance": {"fleet_lp": moved},
        "congestion_weighted_before": before_w,
        "congestion_weighted_after": after_w,
        "congestion_avoided": before_w - after_w,
        "co2_kg_before": before_co2,
        "co2_kg_after": after_co2,
        "co2_kg_delta": before_co2 - after_co2,
        "objective_before": obj_before,
        "objective_after": obj_after,
        "objective_delta": obj_before - obj_after,
        "solver": "linprog_highs",
        "path_label": path_label,
        "lambda_ci": LAMBDA_CI,
        "target_peak_frac": target_peak_frac,
    }
    return OptResult(hourly=out, metrics=metrics, solver="linprog_highs")


def solve_flex_or_fallback(
    demand: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    target_peak_frac: float,
    path_label: str,
) -> OptResult:
    if scored.empty or demand.empty:
        empty = demand.copy() if not demand.empty else pd.DataFrame()
        if not empty.empty and "wasatch_mw" in empty.columns:
            empty = empty.sort_values("hour_ending").reset_index(drop=True)
            empty["wasatch_mw_before"] = empty["wasatch_mw"]
            empty["wasatch_mw_after"] = empty["wasatch_mw"]
        return OptResult(
            hourly=empty,
            metrics={
                "mwh_shifted_total": 0.0,
                "congestion_avoided": 0.0,
                "co2_kg_delta": 0.0,
                "co2_kg_before": 0.0,
                "co2_kg_after": 0.0,
                "objective_before": 0.0,
                "objective_after": 0.0,
                "solver": "skipped",
                "n_cohorts": N_COHORTS,
            },
            solver="skipped",
        )
    return solve_flex_schedule(
        demand,
        scored,
        target_peak_frac=target_peak_frac,
        path_label=path_label,
    )


# Re-export peak fracs for callers
__all__ = [
    "OptResult",
    "solve_flex_schedule",
    "solve_flex_or_fallback",
    "SFH_TARGET_PEAK_FRAC",
    "APT_TARGET_PEAK_FRAC",
    "LAMBDA_CI",
]
