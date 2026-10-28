from __future__ import annotations

import pandas as pd

# Congestion score v0 (documented weights):
#   0.50 * PACE demand percentile within the day
#   0.30 * dirty-mix percentile (ci_kg_per_mwh) within the day
#   0.20 * evening-ramp flag (hour_ending in 18..22)
#
# High score = stay out of traffic. Labeled average/BA mix — not LMP congestion.

W_DEMAND = 0.50
W_DIRTY = 0.30
W_EVENING = 0.20
EVENING_HOURS = set(range(18, 23))
# Jam bands only in this window (rush-hour traffic — never HE1 overnight)
JAM_WINDOW = set(range(16, 23))
MIN_JAM_HOURS = 3


def _pct_rank(series: pd.Series) -> pd.Series:
    if series.isna().all() or series.nunique(dropna=True) <= 1:
        return pd.Series(0.5, index=series.index)
    return series.rank(pct=True, method="average").fillna(0.5)


def _mark_evening_jam(base: pd.DataFrame) -> pd.Series:
    """Top-quartile scores inside HE16–22 only; at least MIN_JAM_HOURS in window."""
    score = base["congestion_score"]
    he = base["hour_ending"].astype(int)
    in_window = he.isin(JAM_WINDOW)
    thresh = float(score.quantile(0.75))
    jam = in_window & (score >= thresh)
    if int(jam.sum()) < MIN_JAM_HOURS:
        window_idx = base.index[in_window]
        if len(window_idx) == 0:
            return pd.Series(False, index=base.index)
        top = (
            base.loc[window_idx]
            .nlargest(min(MIN_JAM_HOURS, len(window_idx)), "congestion_score")
            .index
        )
        jam = pd.Series(False, index=base.index)
        jam.loc[top] = True
    return jam


def congestion_score_v0(
    demand: pd.DataFrame,
    fuel: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Join demand (+ optional fuel CI) on date/hour_ending and emit score in [0, 1].
    """
    base = demand[["date", "hour_ending", "demand_mw"]].copy()
    if fuel is not None and not fuel.empty and "ci_kg_per_mwh" in fuel.columns:
        fuel_cols = [
            c
            for c in fuel.columns
            if c
            not in (
                "date",
                "hour_ending",
            )
        ]
        base = base.merge(
            fuel[["date", "hour_ending", *fuel_cols]],
            on=["date", "hour_ending"],
            how="left",
        )
    else:
        base["ci_kg_per_mwh"] = float("nan")

    base["demand_pct"] = _pct_rank(base["demand_mw"])
    base["dirty_pct"] = _pct_rank(base["ci_kg_per_mwh"])
    base["evening_flag"] = base["hour_ending"].isin(EVENING_HOURS).astype(float)
    base["congestion_score"] = (
        W_DEMAND * base["demand_pct"]
        + W_DIRTY * base["dirty_pct"].fillna(base["demand_pct"])
        + W_EVENING * base["evening_flag"]
    )
    base["is_jam"] = _mark_evening_jam(base)
    return base.sort_values("hour_ending").reset_index(drop=True)


SCORE_DOC = """Congestion score v0
==================
score = 0.50 * demand_pctile(day) + 0.30 * dirty_mix_pctile(day) + 0.20 * evening_ramp
evening_ramp = 1 if hour_ending in {18..22} else 0
dirty_mix from EIA PACE fuel-type CI proxy (kgCO2/MWh), optional EM overlay later.
is_jam = top-quartile scores inside HE16–22 only (evening rush); never overnight HE1.
High score = stay out of traffic. Not LMP / power-flow congestion.
"""
