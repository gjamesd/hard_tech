from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

HVAC_FROM = set(range(18, 23))
HVAC_TO = set(range(14, 18))
EV_FROM = set(range(18, 23))
EV_TO = set(range(11, 16)) | set(range(23, 25)) | set(range(1, 6))
DW_LAUNDRY_FROM = set(range(18, 23))
DW_LAUNDRY_TO = set(range(11, 16)) | set(range(23, 25)) | set(range(1, 3))

APPLIANCES = (
    ("hvac", "wasatch_flex_hvac_mw", HVAC_FROM, HVAC_TO),
    ("ev", "wasatch_flex_ev_mw", EV_FROM, EV_TO),
    ("laundry", "wasatch_flex_laundry_mw", DW_LAUNDRY_FROM, DW_LAUNDRY_TO),
    ("dishwasher", "wasatch_flex_dishwasher_mw", DW_LAUNDRY_FROM, DW_LAUNDRY_TO),
)

N_COHORTS = 48
# Stronger even flatten — shave all jam hours toward target, never gut one into a V
SFH_TARGET_PEAK_FRAC = 0.82
SFH_SLACK_FRAC = 0.22
APT_TARGET_PEAK_FRAC = 0.88
APT_SLACK_FRAC = 0.12
EPS = 1e-6


@dataclass
class ShiftResult:
    hourly: pd.DataFrame
    metrics: dict


def _move_mw_cohorts(
    load: np.ndarray,
    flex: np.ndarray,
    score: np.ndarray,
    from_hours: set[int],
    to_hours: set[int],
    hour_ending: np.ndarray,
    baseline: np.ndarray,
    target_peak: float,
    slack: float,
    ci: np.ndarray | None = None,
    n_cohorts: int = N_COHORTS,
) -> tuple[np.ndarray, np.ndarray, float]:
    """
    Move flex off jam hours, but never below target_peak on a donor.
    That stops the HE18 needle / V when one hour had all the flex.
    """
    new_load = load.copy()
    new_flex = flex.copy()
    moved = 0.0
    if ci is None:
        ci = np.zeros(len(load), dtype=float)

    donors = [i for i, h in enumerate(hour_ending) if h in from_hours and flex[i] > EPS]
    # Round-robin by hour ending so we don't empty the worst hour first
    donors.sort(key=lambda i: (hour_ending[i], -score[i]))
    receivers = [i for i, h in enumerate(hour_ending) if h in to_hours]
    if not donors or not receivers:
        return new_load, new_flex, 0.0

    receivers_ranked = sorted(
        receivers,
        key=lambda i: (score[i], ci[i], new_load[i]),
    )
    caps = np.minimum(target_peak, baseline + slack)

    # Passes over donors until no one is above target with flex left
    for _ in range(n_cohorts * 2):
        progress = False
        for di in donors:
            shave_room = max(0.0, float(new_load[di] - target_peak))
            pool = min(float(new_flex[di]), shave_room)
            if pool <= EPS:
                continue
            take_budget = max(pool / float(n_cohorts), EPS)
            rem = min(take_budget, shave_room)
            for k in range(len(receivers_ranked)):
                if rem <= EPS:
                    break
                ri = receivers_ranked[k]
                room = max(0.0, float(caps[ri] - new_load[ri]))
                if room <= EPS:
                    continue
                take = min(rem, room, max(0.0, float(new_load[di] - target_peak)))
                if take <= EPS:
                    break
                new_load[di] -= take
                new_flex[di] -= take
                new_load[ri] += take
                new_flex[ri] += take
                rem -= take
                moved += take
                progress = True
        if not progress:
            break

    return new_load, new_flex, moved


def appliance_aware_shift(
    demand: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    target_peak_frac: float = SFH_TARGET_PEAK_FRAC,
    slack_frac: float = SFH_SLACK_FRAC,
    n_cohorts: int = N_COHORTS,
) -> ShiftResult:
    df = demand.merge(
        scored[["hour_ending", "congestion_score", "ci_kg_per_mwh", "demand_mw"]],
        on="hour_ending",
        how="left",
        suffixes=("", "_pace"),
    )
    df = df.sort_values("hour_ending").reset_index(drop=True)
    hours = df["hour_ending"].to_numpy()
    score = df["congestion_score"].fillna(0.5).to_numpy()
    ci = df["ci_kg_per_mwh"].to_numpy(dtype=float)
    if np.isnan(ci).all():
        ci = np.zeros_like(score)
    else:
        med = float(np.nanmedian(ci))
        ci = np.where(np.isnan(ci), med, ci)

    baseline = df["wasatch_mw"].to_numpy(dtype=float).copy()
    load = baseline.copy()
    before_peak = float(baseline.max()) if len(baseline) else 0.0
    target_peak = before_peak * float(target_peak_frac)
    slack = float(np.mean(baseline)) * float(slack_frac) if len(baseline) else 0.0

    jam_hours: set[int] = set()
    if "is_jam" in scored.columns:
        jam_hours = set(
            scored.loc[scored["is_jam"], "hour_ending"].astype(int).tolist()
        )
    if not jam_hours:
        jam_hours = (
            set(
                scored.nlargest(5, "congestion_score")["hour_ending"]
                .astype(int)
                .tolist()
            )
            if "congestion_score" in scored.columns and not scored.empty
            else set(range(18, 23))
        )

    moved_by: dict[str, float] = {}
    after_flex_cols: dict[str, np.ndarray] = {}

    for name, col, frm, to in APPLIANCES:
        if col not in df.columns:
            moved_by[name] = 0.0
            after_flex_cols[name] = np.zeros(len(df))
            continue
        flex = df[col].to_numpy(dtype=float).copy()
        load, flex, moved = _move_mw_cohorts(
            load,
            flex,
            score,
            frm,
            to,
            hours,
            baseline=baseline,
            target_peak=target_peak,
            slack=slack,
            ci=ci,
            n_cohorts=n_cohorts,
        )
        moved_by[name] = float(moved)
        after_flex_cols[name] = flex

    df["wasatch_mw_after"] = load
    df["wasatch_mw_before"] = baseline
    for name, arr in after_flex_cols.items():
        df[f"wasatch_flex_{name}_mw_after"] = arr

    after_peak = float(load.max()) if len(load) else 0.0
    after_peak_he = int(hours[int(np.argmax(load))]) if len(load) else 0
    total_moved = sum(moved_by.values())
    new_peak_created = (
        after_peak_he not in jam_hours and after_peak > before_peak * 0.85
    )

    before_w = float(np.dot(baseline, score))
    after_w = float(np.dot(load, score))
    before_co2 = float(np.dot(baseline, ci))
    after_co2 = float(np.dot(load, ci))

    metrics = {
        "peak_mw_before": before_peak,
        "peak_mw_after": after_peak,
        "peak_mw_cut": before_peak - after_peak,
        "after_peak_hour_ending": after_peak_he,
        "new_peak_created": bool(new_peak_created),
        "target_peak_mw": target_peak,
        "n_cohorts": n_cohorts,
        "mwh_shifted_total": total_moved,
        "mwh_shifted_by_appliance": moved_by,
        "congestion_weighted_before": before_w,
        "congestion_weighted_after": after_w,
        "congestion_avoided": before_w - after_w,
        "co2_kg_before": before_co2,
        "co2_kg_after": after_co2,
        "co2_kg_delta": before_co2 - after_co2,
    }
    return ShiftResult(hourly=df, metrics=metrics)
