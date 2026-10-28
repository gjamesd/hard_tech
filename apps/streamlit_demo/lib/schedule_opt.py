from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from .flex_shift import (
    APPLIANCES,
    APT_TARGET_PEAK_FRAC,
    N_COHORTS,
    SFH_TARGET_PEAK_FRAC,
    appliance_aware_shift,
)

LAMBDA_CI = 0.05
EPS = 1e-9


@dataclass
class OptResult:
    hourly: pd.DataFrame
    metrics: dict
    solver: str


def _normalize_ci(ci: np.ndarray) -> np.ndarray:
    if np.isnan(ci).all():
        return np.zeros_like(ci, dtype=float)
    med = float(np.nanmedian(ci))
    filled = np.where(np.isnan(ci), med, ci).astype(float)
    lo, hi = float(filled.min()), float(filled.max())
    if hi - lo < EPS:
        return np.zeros_like(filled)
    return (filled - lo) / (hi - lo)


def _solve_one_appliance(
    load: np.ndarray,
    flex: np.ndarray,
    hours: np.ndarray,
    cost: np.ndarray,
    *,
    from_h: set[int],
    to_h: set[int],
    jam_hours: set[int],
    p_cap: float,
) -> tuple[np.ndarray, float] | None:
    """Return (after_load, mwh_moved) or None if linprog fails / nothing to move."""
    n = len(hours)
    if float(flex.sum()) <= EPS:
        return load.copy(), 0.0

    c_obj = np.concatenate([-cost, cost])
    flex_budget = float(flex.sum())
    bounds: list[tuple[float, float]] = []
    for i, h in enumerate(hours):
        f_max = float(flex[i]) if int(h) in from_h else 0.0
        f_max = min(f_max, max(0.0, float(load[i])))
        bounds.append((0.0, f_max))
    for i, h in enumerate(hours):
        if int(h) not in to_h:
            bounds.append((0.0, 0.0))
            continue
        # Intentional receive windows (pre-cool / midday) may already sit near
        # the daily peak — allow absorbing this appliance's flex budget there.
        room = max(flex_budget, max(0.0, p_cap - float(load[i])))
        bounds.append((0.0, room))

    if all(b[1] <= EPS for b in bounds[:n]) or all(b[1] <= EPS for b in bounds[n:]):
        return load.copy(), 0.0

    a_eq = np.concatenate([np.ones(n), -np.ones(n)])[None, :]
    b_eq = np.array([0.0])

    # Peak cap on non-jam hours that are *not* this appliance's TO window
    # (don't block HVAC pre-cool / midday belly receive hours).
    a_ub_list = []
    b_ub_list = []
    for i, h in enumerate(hours):
        hi = int(h)
        if hi in jam_hours or hi in to_h:
            continue
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
        return None

    f = result.x[:n]
    g = result.x[n:]
    after = np.maximum(load - f + g, 0.0)
    return after, float(f.sum())


def solve_flex_schedule(
    demand: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    target_peak_frac: float = SFH_TARGET_PEAK_FRAC,
    path_label: str = "sfh",
) -> OptResult:
    """
    Sequential per-appliance linprog: each appliance keeps its own FROM/TO windows
    (HVAC → afternoon, EV/wet → midday + overnight). Falls back to heuristic.
    """
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
    load0 = df["wasatch_mw"].to_numpy(dtype=float).copy()
    load = load0.copy()
    score = df["congestion_score"].fillna(0.5).to_numpy(dtype=float)
    ci_raw = (
        df["ci_kg_per_mwh"].to_numpy(dtype=float)
        if "ci_kg_per_mwh" in df.columns
        else np.full(n, np.nan)
    )
    ci_n = _normalize_ci(ci_raw)
    cost = score + LAMBDA_CI * ci_n
    peak = float(load0.max()) if n else 0.0
    p_cap = peak * float(target_peak_frac)

    jam_hours: set[int] = set()
    if "is_jam" in df.columns:
        jam_hours = set(
            df.loc[df["is_jam"].fillna(False), "hour_ending"].astype(int).tolist()
        )
    if not jam_hours:
        jam_hours = set(range(18, 23))

    moved_by: dict[str, float] = {}
    total_moved = 0.0
    any_attempted = False

    for name, col, from_h, to_h in APPLIANCES:
        if col not in df.columns:
            continue
        flex = np.maximum(df[col].to_numpy(dtype=float), 0.0)
        if float(flex.sum()) <= EPS:
            continue
        any_attempted = True
        step = _solve_one_appliance(
            load,
            flex,
            hours,
            cost,
            from_h=set(from_h),
            to_h=set(to_h),
            jam_hours=jam_hours,
            p_cap=p_cap,
        )
        if step is None:
            # Keep prior appliances' moves; skip this one
            continue
        load, moved = step
        if moved > EPS:
            moved_by[name] = moved_by.get(name, 0.0) + moved
            total_moved += moved

    if any_attempted and total_moved <= EPS:
        fb = appliance_aware_shift(
            demand, scored, target_peak_frac=target_peak_frac
        )
        fb.metrics["solver"] = "heuristic_fallback"
        fb.metrics["linprog_message"] = "zero_move"
        fb.metrics["objective_before"] = float(np.dot(load0, cost))
        fb.metrics["objective_after"] = fb.metrics.get(
            "congestion_weighted_after", 0.0
        )
        return OptResult(
            hourly=fb.hourly, metrics=fb.metrics, solver="heuristic_fallback"
        )

    after = load
    obj_before = float(np.dot(load0, cost))
    obj_after = float(np.dot(after, cost))
    before_w = float(np.dot(load0, score))
    after_w = float(np.dot(after, score))
    before_co2 = float(np.dot(load0, np.where(np.isnan(ci_raw), 0.0, ci_raw)))
    after_co2 = float(np.dot(after, np.where(np.isnan(ci_raw), 0.0, ci_raw)))

    after_peak = float(after.max()) if n else 0.0
    after_peak_he = int(hours[int(np.argmax(after))]) if n else 0
    new_peak = after_peak_he not in jam_hours and after_peak > peak * 0.85

    out = df.copy()
    out["wasatch_mw_before"] = load0
    out["wasatch_mw_after"] = after

    metrics = {
        "peak_mw_before": peak,
        "peak_mw_after": after_peak,
        "peak_mw_cut": peak - after_peak,
        "after_peak_hour_ending": after_peak_he,
        "new_peak_created": bool(new_peak),
        "target_peak_mw": p_cap,
        "n_cohorts": N_COHORTS,
        "mwh_shifted_total": total_moved,
        "mwh_shifted_by_appliance": moved_by,
        "congestion_weighted_before": before_w,
        "congestion_weighted_after": after_w,
        "congestion_avoided": before_w - after_w,
        "co2_kg_before": before_co2,
        "co2_kg_after": after_co2,
        "co2_kg_delta": before_co2 - after_co2,
        "objective_before": obj_before,
        "objective_after": obj_after,
        "objective_delta": obj_before - obj_after,
        "solver": "linprog_highs_per_appliance",
        "path_label": path_label,
        "lambda_ci": LAMBDA_CI,
        "target_peak_frac": target_peak_frac,
    }
    return OptResult(
        hourly=out, metrics=metrics, solver="linprog_highs_per_appliance"
    )


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


__all__ = [
    "OptResult",
    "solve_flex_schedule",
    "solve_flex_or_fallback",
    "SFH_TARGET_PEAK_FRAC",
    "APT_TARGET_PEAK_FRAC",
    "LAMBDA_CI",
]
