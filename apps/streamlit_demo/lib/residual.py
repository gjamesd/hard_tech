from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Soft gut-check bands: Wasatch vs EIA BA at face value.
# Target ≈ 25% of EIA (≈ 0.75 × EIA Utah ~⅓ residential).
# Peak can run higher on hot AC evenings.
PEAK_SHARE_LO, PEAK_SHARE_HI = 0.18, 0.45
ENERGY_SHARE_LO, ENERGY_SHARE_HI = 0.20, 0.32
EVENING_HOURS = set(range(18, 23))

# EIA hourly BA demand at face value (no retail-customer haircut).
UTAH_RMP_CUSTOMER_SHARE = 1.0


@dataclass
class ResidualReport:
    hourly: pd.DataFrame
    wasatch_peak_mw: float
    pace_peak_mw: float
    utah_peak_mw: float
    residual_peak_mw: float
    wasatch_mwh: float
    pace_mwh: float
    utah_mwh: float
    residual_mwh: float
    peak_share: float
    energy_share: float
    evening_residual_mwh: float
    residual_always_nonneg: bool
    peak_share_ok: bool
    energy_share_ok: bool
    gut_check: str  # Pass | Review
    gut_notes: list[str]
    utah_share: float


def align_hourly(
    wasatch: pd.DataFrame,
    pace: pd.DataFrame,
    *,
    wasatch_col: str = "wasatch_mw",
    pace_col: str = "demand_mw",
    utah_share: float = UTAH_RMP_CUSTOMER_SHARE,
) -> ResidualReport:
    """
    Align Wasatch residential MW with EIA BA demand at face value.
    utah_mw = utah_share * PACE_MW (default share=1.0 → no haircut).
    residual = utah_mw - wasatch_mw → non-Wasatch-residential BA load
    (C&I, institutional, other counties) — labeled bag, not a census split.
    Gut-check energy share vs target ≈ 25% of EIA BA.
    """
    w = wasatch[["hour_ending", wasatch_col]].rename(columns={wasatch_col: "wasatch_mw"})
    p = pace[["hour_ending", pace_col]].rename(columns={pace_col: "pace_mw"})
    hourly = (
        w.merge(p, on="hour_ending", how="outer")
        .sort_values("hour_ending")
        .reset_index(drop=True)
    )
    hourly["wasatch_mw"] = hourly["wasatch_mw"].fillna(0.0)
    hourly["pace_mw"] = hourly["pace_mw"].fillna(0.0)
    hourly["utah_mw"] = hourly["pace_mw"] * float(utah_share)
    hourly["residual_mw"] = hourly["utah_mw"] - hourly["wasatch_mw"]

    wasatch_peak = float(hourly["wasatch_mw"].max())
    pace_peak = float(hourly["pace_mw"].max())
    utah_peak = float(hourly["utah_mw"].max())
    residual_peak = float(hourly["residual_mw"].max())
    wasatch_mwh = float(hourly["wasatch_mw"].sum())
    pace_mwh = float(hourly["pace_mw"].sum())
    utah_mwh = float(hourly["utah_mw"].sum())
    residual_mwh = float(hourly["residual_mw"].sum())
    peak_share = wasatch_peak / utah_peak if utah_peak > 1e-9 else float("nan")
    energy_share = wasatch_mwh / utah_mwh if utah_mwh > 1e-9 else float("nan")
    evening = hourly["hour_ending"].isin(list(EVENING_HOURS))
    evening_residual = float(hourly.loc[evening, "residual_mw"].sum())

    residual_ok = bool(np.all(hourly["residual_mw"].to_numpy() >= -1e-6))
    peak_ok = PEAK_SHARE_LO <= peak_share <= PEAK_SHARE_HI
    energy_ok = ENERGY_SHARE_LO <= energy_share <= ENERGY_SHARE_HI

    notes: list[str] = []
    if not residual_ok:
        notes.append(
            "Wasatch exceeds EIA BA demand in at least one hour — scale bug likely."
        )
    if not peak_ok:
        notes.append(
            f"Peak residential share {peak_share:.0%} outside soft band "
            f"{PEAK_SHARE_LO:.0%}–{PEAK_SHARE_HI:.0%}."
        )
    if not energy_ok:
        notes.append(
            f"Daily residential energy share {energy_share:.0%} outside soft band "
            f"{ENERGY_SHARE_LO:.0%}–{ENERGY_SHARE_HI:.0%} "
            f"(target ≈ 25% of EIA)."
        )
    if residual_ok and evening_residual > 0.5 * max(residual_mwh, 1e-9):
        notes.append("Evening residual is large — jam is not only homes.")

    gut = "Pass" if (residual_ok and peak_ok and energy_ok) else "Review"
    if residual_ok and not notes:
        notes.append(
            "Residual positive; Wasatch share of EIA BA demand near "
            "≈ 25% target (75% of Utah residential)."
        )

    return ResidualReport(
        hourly=hourly,
        wasatch_peak_mw=wasatch_peak,
        pace_peak_mw=pace_peak,
        utah_peak_mw=utah_peak,
        residual_peak_mw=residual_peak,
        wasatch_mwh=wasatch_mwh,
        pace_mwh=pace_mwh,
        utah_mwh=utah_mwh,
        residual_mwh=residual_mwh,
        peak_share=peak_share,
        energy_share=energy_share,
        evening_residual_mwh=evening_residual,
        residual_always_nonneg=residual_ok,
        peak_share_ok=peak_ok,
        energy_share_ok=energy_ok,
        gut_check=gut,
        gut_notes=notes,
        utah_share=float(utah_share),
    )


def pace_source_label(meta: dict | None, has_eia_demand: bool = False) -> str:
    if has_eia_demand:
        return "EIA region-data (BA=PACE)"
    src = (meta or {}).get("grid_source") or "missing"
    if src == "synthetic_pace_proxy":
        return "Labeled synthetic proxy (not EIA)"
    if src == "eia":
        return "EIA region-data (BA=PACE)"
    if src == "forecast":
        return "missing EIA"
    return str(src)
