#!/usr/bin/env python3
"""Wasatch Front · PACE traffic report — always-on as-of forecast."""

from __future__ import annotations

import json
import sys
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parents[1]
sys.path.insert(0, str(APP_DIR))
load_dotenv(ROOT / ".env")

from lib.agent_plan import build_agent_plan  # noqa: E402
from lib.aggregate import available_dates, load_wasatch_day  # noqa: E402
from lib.congestion import congestion_score_v0  # noqa: E402
from lib.eia_pace import load_nearest_fuel, load_or_fetch_day  # noqa: E402
from lib.forecast import predict_day  # noqa: E402
from lib.interventions import apply_relief_stack  # noqa: E402
from lib.meters import load_house_casita_best, propose_house_shift  # noqa: E402
from lib.paths import (  # noqa: E402
    EFFECTIVE_MULT,
    GRID_CACHE_DIR,
    HERO_DATES,
    HERO_LABELS,
    IMPLIED_STOCK,
    INTENSITY,
    MODEL_META_PATH,
    SCALE,
    WASATCH_MODEL_PATH,
)
from lib.residual import (  # noqa: E402
    UTAH_RMP_CUSTOMER_SHARE,
    align_hourly,
    pace_source_label,
)

st.set_page_config(
    page_title="Wasatch Grid Traffic",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; max-width: 1100px; padding-bottom: 2rem; }
      h1 { font-size: 1.65rem !important; letter-spacing: -0.02em; }
      h2, h3 { letter-spacing: -0.01em; }
      div[data-testid="stMetricValue"] { font-size: 1.25rem; }
      div[data-testid="stMetricLabel"] { font-size: 0.85rem; }
      .headline-box {
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 8px;
        padding: 0.85rem 1rem;
        margin: 0.5rem 0 1rem 0;
        background: rgba(56, 161, 105, 0.08);
      }
      @media (max-width: 640px) {
        h1 { font-size: 1.3rem !important; }
        div[data-testid="stMetricValue"] { font-size: 1.05rem; }
        .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def _dates(_mult: float = EFFECTIVE_MULT) -> list[str]:
    try:
        return available_dates()
    except FileNotFoundError:
        return []


@st.cache_data(show_spinner="Forecasting day…")
def _forecast(date: str, _mult: float = EFFECTIVE_MULT) -> dict:
    return predict_day(date)


@st.cache_data(show_spinner=False)
def _actual_wasatch(date: str, _mult: float = EFFECTIVE_MULT) -> pd.DataFrame:
    try:
        return load_wasatch_day(date)
    except FileNotFoundError:
        return pd.DataFrame()


@st.cache_data(show_spinner="Pulling EIA PACE…")
def _pace_eia(date: str, _mult: float = EFFECTIVE_MULT) -> dict:
    try:
        pack = load_or_fetch_day(date)
    except Exception as exc:  # noqa: BLE001
        return {
            "demand": pd.DataFrame(),
            "fuel": pd.DataFrame(),
            "source": "error",
            "error": str(exc),
        }
    demand = pack.get("demand")
    fuel = pack.get("fuel")
    if not isinstance(demand, pd.DataFrame):
        demand = pd.DataFrame()
    if not isinstance(fuel, pd.DataFrame):
        fuel = pd.DataFrame()
    source = str(pack.get("source") or "")
    marker = GRID_CACHE_DIR / date / "source.txt"
    if marker.is_file():
        source = marker.read_text().strip()
    return {"demand": demand, "fuel": fuel, "source": source, "error": ""}


def _jam_hours(scored: pd.DataFrame) -> list[int]:
    if scored.empty or "is_jam" not in scored.columns:
        return []
    return sorted(scored.loc[scored["is_jam"], "hour_ending"].astype(int).tolist())


def _add_jam_bands(fig: go.Figure, scored: pd.DataFrame, opacity: float = 0.22) -> None:
    """Paint jam as contiguous blocks on exact HE ticks (e.g. 18→22), not ±0.5 pads."""
    hours = _jam_hours(scored)
    if not hours:
        return
    start = hours[0]
    prev = hours[0]
    blocks: list[tuple[int, int]] = []
    for he in hours[1:]:
        if he == prev + 1:
            prev = he
            continue
        blocks.append((start, prev))
        start = prev = he
    blocks.append((start, prev))
    for lo, hi in blocks:
        fig.add_vrect(
            x0=lo,
            x1=hi,
            fillcolor=f"rgba(245, 101, 101, {opacity})",
            line_width=0,
            layer="below",
        )


def _meter_chart(frame: pd.DataFrame, name: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=frame["hour_ending"],
            y=frame["baseload_kwh"],
            name="Baseload",
            marker_color="#4a5568",
        )
    )
    fig.add_trace(
        go.Bar(
            x=frame["hour_ending"],
            y=frame["flexible_kwh"],
            name="Flexible",
            marker_color="#38a169",
        )
    )
    if "kwh_after" in frame.columns:
        fig.add_trace(
            go.Scatter(
                x=frame["hour_ending"],
                y=frame["kwh_after"],
                name="After agent",
                line=dict(color="#dd6b20", width=3),
            )
        )
    fig.update_layout(
        barmode="stack",
        height=260,
        margin=dict(l=8, r=8, t=32, b=8),
        legend=dict(orientation="h", y=1.15),
        title=name,
        xaxis_title="Hour ending",
        yaxis_title="kWh",
    )
    return fig


def _pick_default_day(dates: list[str]) -> date_cls:
    for hero in HERO_DATES:
        if hero in dates:
            return pd.to_datetime(hero).date()
    return pd.to_datetime(dates[min(len(dates) - 1, 180)]).date()


def main() -> None:
    st.title("Wasatch Front · grid traffic")
    st.caption(
        "Traffic report for PacifiCorp East · agents that schedule around clean power. "
        f"Wasatch residential proxy: 5k RMP-seeded homes × {SCALE} × intensity {INTENSITY}."
    )

    dates = _dates()
    if not dates:
        st.error(
            "No Wasatch day series found. Run the deploy pack script or "
            "`aggregate_wasatch_day.py --all-dates`."
        )
        return
    if not WASATCH_MODEL_PATH.is_file():
        st.warning(
            "Forecast models missing. Run `scripts/rmp/train_demand_forecast.py` "
            "or `pack_streamlit_deploy.py`, then refresh."
        )
        return

    data_start = pd.to_datetime(dates[0]).date()
    data_end = pd.to_datetime(dates[-1]).date()
    min_history_days = 7
    min_d = data_start + timedelta(days=min_history_days)
    max_d = data_end + timedelta(days=1)
    if "demo_day" not in st.session_state:
        st.session_state.demo_day = _pick_default_day(dates)
    if st.session_state.demo_day < min_d:
        st.session_state.demo_day = min_d
    if st.session_state.demo_day > max_d:
        st.session_state.demo_day = max_d

    st.markdown("**Demo day**")
    st.caption(
        f"Wasatch sample covers **{data_start.isoformat()} → {data_end.isoformat()}** "
        f"({len(dates)} days). "
        f"Earliest selectable day is **{min_d.isoformat()}** "
        f"(needs ≥{min_history_days} prior days for lag features). "
        f"Latest is **{max_d.isoformat()}** (day-ahead HGB forecast past the sample)."
    )

    def _set_demo_day(d: date_cls) -> None:
        st.session_state.demo_day = d
        st.session_state.date_picker = d

    hero_labels = [
        *[(HERO_LABELS.get(h, h), pd.to_datetime(h).date()) for h in HERO_DATES],
        ("Tomorrow forecast", max_d),
    ]
    hero_cols = st.columns(len(hero_labels))
    for i, (label, day_v) in enumerate(hero_labels):
        with hero_cols[i]:
            if st.button(
                f"{label}\n{day_v.isoformat()}",
                key=f"hero_{day_v.isoformat()}",
                use_container_width=True,
            ):
                _set_demo_day(day_v)
                st.rerun()

    date = st.date_input(
        "Day to predict",
        value=st.session_state.demo_day,
        min_value=min_d,
        max_value=max_d,
        help=(
            f"Data window {data_start} → {data_end}. "
            f"Picker starts {min_d} (need {min_history_days}+ history days) "
            f"and ends {max_d} (next-day forecast)."
        ),
        key="date_picker",
    )
    st.session_state.demo_day = date
    date_s = date.isoformat()

    st.sidebar.header("Agent levers")
    show_actuals = st.sidebar.checkbox(
        "Show actuals overlay",
        value=True,
        help="When this day exists in the synthetic universe, overlay actual MW.",
    )
    use_flex = st.sidebar.checkbox(
        "Appliance + EV flex-shift",
        value=True,
        help="Primary congestion lever — retime flexible load (works at 0% solar).",
    )
    adoption = st.sidebar.slider(
        "Plug-in solar adoption % (SFH only)",
        min_value=0,
        max_value=60,
        value=50,
        help=(
            "HB 340: 4 panels ≤1.2 kW → wall outlet on SFH (~75% of Wasatch). "
            "Apts never get kits."
        ),
    )
    use_battery = (
        st.sidebar.radio("Storage (SFH only)", ["No battery", "With battery"], index=1)
        == "With battery"
    )
    st.sidebar.caption(
        "Flex works with solar at **0%**. "
        "**With battery** adds 3 more panels (~0.8 kW) into the battery only "
        "(EcoFlow-style) — more evening power, still ≤1.2 kW on the AC plug. "
        "Agent **proposes** windows — no live RMP control."
    )

    try:
        forecast_bundle = _forecast(date_s)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Forecast failed for {date_s}: {exc}")
        return

    hist_end = forecast_bundle.get("history_end", "?")
    n_hist = forecast_bundle.get("n_history_days", 0)

    weather = forecast_bundle["weather"]
    if not isinstance(weather, pd.DataFrame):
        weather = pd.DataFrame()

    wasatch_hat = forecast_bundle["wasatch"].rename(columns={"mw_hat": "wasatch_mw"})
    for col in (
        "wasatch_flex_hvac_mw",
        "wasatch_flex_laundry_mw",
        "wasatch_flex_dishwasher_mw",
        "wasatch_flex_ev_mw",
        "wasatch_flexible_mw",
    ):
        wasatch_hat[col] = 0.0

    actual = _actual_wasatch(date_s)
    if use_flex and not actual.empty:
        for col in (
            "wasatch_flex_hvac_mw",
            "wasatch_flex_laundry_mw",
            "wasatch_flex_dishwasher_mw",
            "wasatch_flex_ev_mw",
            "wasatch_flexible_mw",
        ):
            if col in actual.columns:
                merged = wasatch_hat.merge(
                    actual[["hour_ending", col, "wasatch_mw"]].rename(
                        columns={"wasatch_mw": "actual_mw", col: "flex_src"}
                    ),
                    on="hour_ending",
                    how="left",
                )
                share = (
                    merged["flex_src"] / merged["actual_mw"].clip(lower=1e-6)
                ).fillna(0.0)
                wasatch_hat[col] = (
                    wasatch_hat["wasatch_mw"] * share.clip(0, 0.95)
                ).to_numpy()

    pace_pack = _pace_eia(date_s)
    pace_eia = pace_pack["demand"]
    pace_source = str(pace_pack.get("source") or "").lower()
    has_eia_demand = (
        isinstance(pace_eia, pd.DataFrame)
        and not pace_eia.empty
        and "demand_mw" in pace_eia.columns
        and pace_source == "eia"
    )
    fuel = pace_pack.get("fuel")
    if not isinstance(fuel, pd.DataFrame):
        fuel = pd.DataFrame()
    fuel_proxy_from = ""
    if not has_eia_demand or fuel.empty:
        prior_fuel, prior_day = load_nearest_fuel(date_s)
        if not prior_fuel.empty:
            fuel = prior_fuel
            fuel_proxy_from = prior_day

    pace_demand = pace_eia if has_eia_demand else pd.DataFrame()
    pace_forecast_used = False
    if not has_eia_demand:
        pace_hat = forecast_bundle.get("pace")
        if (
            isinstance(pace_hat, pd.DataFrame)
            and not pace_hat.empty
            and "mw_hat" in pace_hat.columns
        ):
            pace_demand = pace_hat.rename(columns={"mw_hat": "demand_mw"}).copy()
            pace_demand["date"] = date_s
            pace_forecast_used = True

    has_ba_demand = (
        isinstance(pace_demand, pd.DataFrame)
        and not pace_demand.empty
        and "demand_mw" in pace_demand.columns
    )
    meta = {
        "grid_ok": has_eia_demand,
        "grid_source": (
            "eia"
            if has_eia_demand
            else ("hgb_pace_forecast" if pace_forecast_used else pace_source or "missing")
        ),
    }
    scored = (
        congestion_score_v0(
            pace_demand, fuel if not fuel.empty else None
        )
        if has_ba_demand
        else pd.DataFrame()
    )

    if weather.empty:
        weather = pd.DataFrame(
            {"hour_ending": list(range(1, 25)), "shortwave_wm2": [0.0] * 24}
        )
    relief = apply_relief_stack(
        wasatch_hat,
        weather,
        scored,
        adoption_pct=float(adoption),
        use_battery=use_battery,
        use_flex=use_flex,
    )
    metrics = relief.metrics
    jam_list = _jam_hours(scored)
    jam_txt = ", ".join(f"HE{h}" for h in jam_list) if jam_list else "—"

    # --- §1 Metrics (must be first visual after day picker) ---
    evening_before = float(metrics.get("evening_mwh_before", 0) or 0)
    evening_off = float(metrics.get("headline_mwh_off_rush", 0) or 0)
    jam_cut_pct = (
        100.0 * evening_off / evening_before if evening_before > 1e-9 else 0.0
    )
    # --- §1 Wasatch day-ahead ---
    st.subheader("1 · Wasatch day-ahead prediction")
    st.caption(
        f"HGB on lagged Wasatch MW (1h…1 week) + Millcreek weather. "
        f"Trained through **{hist_end}** ({n_hist} prior days)."
    )
    if isinstance(n_hist, int) and n_hist < min_history_days:
        st.warning(
            f"Fewer than {min_history_days} history days before this date — "
            "forecast may be weak; pick a later day."
        )
    fig_pred = go.Figure()
    fig_pred.add_trace(
        go.Scatter(
            x=wasatch_hat["hour_ending"],
            y=wasatch_hat["wasatch_mw"],
            name="Wasatch predicted (HGB)",
            line=dict(color="#2b6cb0", width=3),
        )
    )
    if show_actuals and not actual.empty:
        fig_pred.add_trace(
            go.Scatter(
                x=actual["hour_ending"],
                y=actual["wasatch_mw"],
                name="Wasatch proxy actual (scaled synth)",
                line=dict(color="#2b6cb0", width=2, dash="dot"),
            )
        )
        mae = float(
            np.abs(
                wasatch_hat.sort_values("hour_ending")["wasatch_mw"].to_numpy()
                - actual.sort_values("hour_ending")["wasatch_mw"].to_numpy()
            ).mean()
        )
        st.caption(f"Predicted vs on-disk proxy MAE: **{mae:.0f} MW**")
    elif actual.empty:
        st.caption(
            f"No on-disk Wasatch actual for **{date_s}** — showing model forecast only "
            "(typical for the day-after-sample / tomorrow button)."
        )
    fig_pred.update_layout(
        height=300,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", y=1.12),
        xaxis_title="Hour ending",
        yaxis_title="Wasatch MW",
    )
    st.plotly_chart(fig_pred, use_container_width=True)

    # --- §2 Scale / gut-check ---
    st.subheader("2 · Scale check (gut-check)")
    if MODEL_META_PATH.is_file():
        meta_m = json.loads(MODEL_META_PATH.read_text())
        if "wasatch" in meta_m:
            holdout = meta_m["wasatch"].get("holdout_date") or meta_m[
                "wasatch"
            ].get("holdout_end", "—")
            st.caption(
                f"Wasatch holdout MAE **{meta_m['wasatch']['mae_mw']:.0f} MW** "
                f"({holdout}). Day-ahead curve is in §1."
            )

    with st.expander("How we know Wasatch isn’t inventing MW (gut-check)", expanded=True):
        wasatch_for_residual = actual if not actual.empty else wasatch_hat
        _w_peak = float(wasatch_for_residual["wasatch_mw"].max())
        _w_mwh = float(wasatch_for_residual["wasatch_mw"].sum())
        _per_home_kwh = (_w_mwh * 1000.0) / float(IMPLIED_STOCK)
        st.caption(
            f"Peak **{_w_peak:.0f} MW** ÷ {IMPLIED_STOCK:,} homes ≈ "
            f"**{_w_peak * 1e6 / IMPLIED_STOCK:.0f} W/home** coincident; "
            f"day energy ≈ **{_per_home_kwh:.1f} kWh/home**. "
            "Calibrated residential share of EIA (~25–33% peak); gut-check is regression, not proof. "
            "EIA timestamps are UTC → America/Denver."
        )
        if not has_eia_demand:
            st.warning("Gut-check skipped — need EIA hourly demand for this day.")
        else:
            residual = align_hourly(
                wasatch_for_residual,
                pace_eia,
                utah_share=UTAH_RMP_CUSTOMER_SHARE,
            )
            pace_label = pace_source_label(meta, has_eia_demand=has_eia_demand)
            st.caption(
                "Same MW axis: Wasatch residential vs rest of **PACE BA**. "
                f"Source: {pace_label}."
            )
            rh_res = residual.hourly
            fig_stack = go.Figure()
            fig_stack.add_trace(
                go.Scatter(
                    x=rh_res["hour_ending"],
                    y=rh_res["wasatch_mw"],
                    name="Wasatch residential",
                    stackgroup="one",
                    fillcolor="rgba(43, 108, 176, 0.65)",
                    line=dict(width=0.5, color="#2b6cb0"),
                )
            )
            fig_stack.add_trace(
                go.Scatter(
                    x=rh_res["hour_ending"],
                    y=rh_res["residual_mw"].clip(lower=0),
                    name="Rest of PACE BA (C&I, institutional, non-Wasatch)",
                    stackgroup="one",
                    fillcolor="rgba(160, 174, 192, 0.65)",
                    line=dict(width=0.5, color="#718096"),
                )
            )
            fig_stack.add_trace(
                go.Scatter(
                    x=rh_res["hour_ending"],
                    y=rh_res["utah_mw"],
                    name="EIA BA demand",
                    line=dict(color="#c53030", width=3, dash="dash"),
                )
            )
            fig_stack.update_layout(
                height=320,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", y=1.14),
                xaxis_title="Hour ending",
                yaxis_title="MW (same axis)",
            )
            st.plotly_chart(fig_stack, use_container_width=True)
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Res share of EIA peak", f"{residual.peak_share:.0%}")
            g2.metric("Res share of EIA MWh", f"{residual.energy_share:.0%}")
            g3.metric("Residual peak", f"{residual.residual_peak_mw:.0f} MW")
            g4.metric("Gut-check", residual.gut_check)
            st.caption(" ".join(residual.gut_notes))
            if not residual.residual_always_nonneg:
                st.error(
                    "Wasatch exceeds EIA BA demand in at least one hour — "
                    "treat scale assumptions as broken."
                )
            elif residual.gut_check == "Review":
                st.warning(
                    "Gut-check **Review** — share outside soft band "
                    "(energy 20–32%, peak 18–45%); still usable if labeled."
                )
    # --- §3 Agent ---
    st.subheader("3 · Agent schedule + solar")
    st.caption(
        "Proposal only — no live RMP / device control. "
        "BA jam is the map; exposure drop is the proof."
    )
    plan = build_agent_plan(
        scored,
        use_flex=use_flex,
        use_battery=use_battery,
        adoption_pct=float(adoption),
    )
    if not plan:
        st.info("Turn on **Appliance + EV flex-shift** (or SFH solar/battery) to see steps.")
    else:
        for step in plan:
            st.markdown(f"- **{step.asset}:** {step.action}")

    if metrics.get("new_peak_created"):
        st.warning(
            f"After-agent peak is at HE{metrics.get('after_peak_hour_ending', '?')} "
            "(outside original jam) — watching for a relocated rush."
        )
    else:
        st.caption(
            f"After-agent peak HE{metrics.get('after_peak_hour_ending', '—')} "
            f"under flatten target {metrics.get('target_peak_mw', 0):.0f} MW."
        )
    co2_delta = float(metrics.get("co2_kg_delta", 0) or 0)
    if abs(co2_delta) > 1:
        if co2_delta > 0:
            st.caption(
                f"Average-BA-mix CO₂ proxy fell by **{co2_delta:.0f} kg** "
                f"(before {metrics.get('co2_kg_before', 0):.0f} → "
                f"after {metrics.get('co2_kg_after', 0):.0f}). Not marginal / MOER."
            )
        else:
            st.caption(
                f"Average-BA-mix CO₂ proxy rose by **{abs(co2_delta):.0f} kg** "
                "(evening dirty mix vs daytime average — labeled average fuel CI, "
                "not marginal abatement)."
            )

    rh = relief.hourly
    he = rh["hour_ending"].tolist()
    base_y = rh["wasatch_mw_baseline"].tolist()
    after_y = rh["wasatch_mw_after"].tolist()
    fig_r = go.Figure()
    _add_jam_bands(fig_r, scored, 0.18)
    fig_r.add_trace(
        go.Scatter(
            x=he + he[::-1],
            y=base_y + after_y[::-1],
            fill="toself",
            fillcolor="rgba(56, 161, 105, 0.22)",
            line=dict(width=0),
            name="Demand shifted off",
            hoverinfo="skip",
        )
    )
    fig_r.add_trace(
        go.Scatter(
            x=he,
            y=base_y,
            name="Predicted baseline",
            line=dict(color="#2b6cb0", width=3),
        )
    )
    fig_r.add_trace(
        go.Scatter(
            x=he,
            y=after_y,
            name="After agent",
            line=dict(color="#38a169", width=3),
        )
    )
    if "solar_to_load_mw" in rh.columns and float(rh["solar_to_load_mw"].sum()) > 0:
        fig_r.add_trace(
            go.Bar(
                x=he,
                y=rh["solar_to_load_mw"],
                name="4-panel AC → load",
                marker_color="#68d391",
                opacity=0.45,
            )
        )
    if (
        "battery_direct_mw" in rh.columns
        and float(rh["battery_direct_mw"].sum()) > 0
    ):
        fig_r.add_trace(
            go.Bar(
                x=he,
                y=rh["battery_direct_mw"],
                name="3-panel → battery",
                marker_color="#d69e2e",
                opacity=0.55,
            )
        )
    if "battery_charge_mw" in rh.columns and float(rh["battery_charge_mw"].sum()) > 0:
        fig_r.add_trace(
            go.Bar(
                x=he,
                y=rh["battery_charge_mw"],
                name="Battery charge",
                marker_color="#f6e05e",
                opacity=0.75,
            )
        )
    if (
        "battery_discharge_mw" in rh.columns
        and float(rh["battery_discharge_mw"].sum()) > 0
    ):
        fig_r.add_trace(
            go.Bar(
                x=he,
                y=rh["battery_discharge_mw"],
                name="Battery discharge",
                marker_color="#9f7aea",
                opacity=0.75,
            )
        )
    fig_r.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", y=1.14),
        xaxis_title="Hour ending",
        yaxis_title="Wasatch MW",
        barmode="overlay",
        annotations=[
            dict(
                x=0.99,
                y=0.98,
                xref="paper",
                yref="paper",
                text=(
                    f"Jam −{jam_cut_pct:.0f}% · "
                    f"evening −{evening_off:.0f} MWh"
                ),
                showarrow=False,
                align="right",
                bgcolor="rgba(26,32,44,0.65)",
                font=dict(size=12, color="#e2e8f0"),
            )
        ],
    )
    st.plotly_chart(fig_r, use_container_width=True)
    st.caption(
        "Green band = demand off baseline. Green bars = 4-panel HB 340 → load. "
        "Gold bars = extra 3 panels → battery only (not on the 1.2 kW AC cap). "
        "Yellow/purple = battery charge/discharge (equal MWh) — more evening power with the kit."
    )

    if not scored.empty and not relief.hourly.empty:
        st.markdown("**Fleet congestion exposure** (Wasatch MW × BA score)")
        exp = relief.hourly.merge(
            scored[["hour_ending", "congestion_score"]],
            on="hour_ending",
            how="left",
        ).sort_values("hour_ending")
        c = exp["congestion_score"].fillna(0.5).to_numpy(dtype=float)
        exp_before = exp["wasatch_mw_baseline"].to_numpy(dtype=float) * c
        exp_after = exp["wasatch_mw_after"].to_numpy(dtype=float) * c
        sum_b, sum_a = float(exp_before.sum()), float(exp_after.sum())
        e1, e2, e3 = st.columns(3)
        e1.metric("Exposure before", f"{sum_b:.0f}", help="Σ baseline MW × score")
        e2.metric("Exposure after", f"{sum_a:.0f}", help="Σ after-agent MW × score")
        e3.metric("Exposure cut", f"{sum_b - sum_a:.0f}")
        fig_exp = go.Figure()
        _add_jam_bands(fig_exp, scored, 0.18)
        fig_exp.add_trace(
            go.Scatter(
                x=exp["hour_ending"],
                y=exp_before,
                name="Before (baseline × score)",
                line=dict(color="#2b6cb0", width=3),
            )
        )
        fig_exp.add_trace(
            go.Scatter(
                x=exp["hour_ending"],
                y=exp_after,
                name="After agent (load × score)",
                line=dict(color="#38a169", width=3),
            )
        )
        fig_exp.update_layout(
            height=280,
            margin=dict(l=8, r=8, t=8, b=8),
            legend=dict(orientation="h", y=1.12),
            xaxis_title="Hour ending",
            yaxis_title="MW × score",
        )
        st.plotly_chart(fig_exp, use_container_width=True)
        st.caption(
            "Green below blue = stayed out of traffic "
            "(less fleet MW on high-score hours)."
        )

    st.subheader("4 · Impact metrics")
    st.markdown(
        f"""
        <div class="headline-box">
          <strong>How we manage congestion:</strong>
          retime residential flex out of the rush into cleaner hours.
          Jam tonight: <strong>{jam_txt}</strong> ·
          evening MWh off rush:
          <strong>{evening_off:.0f}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )
    h1, h2, h3 = st.columns(3)
    h1.metric("Evening MWh off rush", f"{evening_off:.0f}")
    h2.metric(
        "Jam-hour demand cut",
        f"{jam_cut_pct:.0f}%",
        help="Share of Wasatch MWh removed inside BA jam hours, not daily max MW.",
    )
    h3.metric(
        "Congestion avoided",
        f"{metrics.get('congestion_avoided', 0):.0f}",
        help="Drop in congestion-weighted MWh·score",
    )
    d1, d2, d3 = st.columns(3)
    d1.metric("Flex shifted", f"{metrics.get('mwh_shifted_total', 0):.0f} MWh")
    d2.metric(
        "Battery charge / discharge",
        f"{metrics.get('battery_mwh_charged', 0):.0f} / "
        f"{metrics.get('battery_mwh_discharged', 0):.0f}",
    )
    d3.metric(
        "Solar live / batt-PV",
        f"{metrics.get('solar_live_mwh', metrics.get('solar_to_load_mwh', 0)):.0f} / "
        f"{metrics.get('battery_direct_mwh', 0):.0f}",
    )

    # --- Supporting: BA traffic (below the main 1–4 path) ---
    with st.expander("BA traffic — when the grid is jammed", expanded=False):
        st.caption(
            "Score = 0.5·within-day demand rank + 0.3·dirty-mix rank + 0.2·evening flag. "
            "Jam = top scores inside HE16–22. Labeled BA stress — not LMP congestion."
        )
        if not has_ba_demand:
            st.error(
                "No BA demand for this day (EIA cache miss and PACE forecast unavailable). "
                + (pace_pack.get("error") or f"source={pace_source or 'missing'}")
            )
        else:
            if pace_forecast_used:
                st.info(
                    f"**Labeled HGB day-ahead PACE forecast** for {date_s} "
                    "(no EIA actual published for this day). Not live EIA-930."
                )
            fig_e = go.Figure()
            _add_jam_bands(fig_e, scored, 0.22)
            fig_e.add_trace(
                go.Scatter(
                    x=pace_demand["hour_ending"],
                    y=pace_demand["demand_mw"],
                    name=(
                        "PACE HGB forecast"
                        if pace_forecast_used
                        else "EIA BA demand (actual)"
                    ),
                    line=dict(color="#c53030", width=3),
                )
            )
            # When we have EIA actuals, also overlay the day-ahead PACE HGB prediction
            pace_hat_overlay = forecast_bundle.get("pace")
            if (
                has_eia_demand
                and isinstance(pace_hat_overlay, pd.DataFrame)
                and not pace_hat_overlay.empty
                and "mw_hat" in pace_hat_overlay.columns
            ):
                fig_e.add_trace(
                    go.Scatter(
                        x=pace_hat_overlay["hour_ending"],
                        y=pace_hat_overlay["mw_hat"],
                        name="PACE HGB day-ahead (model)",
                        line=dict(color="#c53030", width=2, dash="dot"),
                    )
                )
            fig_e.update_layout(
                height=280,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="Hour ending (America/Denver)",
                yaxis_title="MW",
                legend=dict(orientation="h", y=1.12),
            )
            st.plotly_chart(fig_e, use_container_width=True)
            ba_peak = float(pace_demand["demand_mw"].max())
            ba_peak_he = int(
                pace_demand.loc[pace_demand["demand_mw"].idxmax(), "hour_ending"]
            )
            src_lbl = "Forecast" if pace_forecast_used else "EIA"
            st.caption(
                f"{src_lbl} peak **{ba_peak:.0f} MW** at HE{ba_peak_he}. "
                f"Red bands = evening jam: **{jam_txt}**."
            )

        left, right = st.columns(2)
        with left:
            st.markdown("**Fuel mix** (how the BA meets load)")
            if not fuel.empty:
                if fuel_proxy_from:
                    st.caption(
                        f"Prior-day EIA mix from **{fuel_proxy_from}** "
                        "(labeled proxy for CI — not same-day fuel)."
                    )
                fuel_plot = fuel.sort_values("hour_ending")
                stack_cols = [
                    c
                    for c in (
                        "solar",
                        "wind",
                        "hydro",
                        "nuclear",
                        "natural_gas",
                        "coal",
                        "other",
                        "oil",
                    )
                    if c in fuel_plot.columns
                ]
                fig_fuel = go.Figure()
                palette = {
                    "solar": "#f6e05e",
                    "wind": "#63b3ed",
                    "hydro": "#4fd1c5",
                    "nuclear": "#b794f4",
                    "natural_gas": "#fc8181",
                    "coal": "#718096",
                    "other": "#a0aec0",
                    "oil": "#ed8936",
                }
                for col in stack_cols:
                    fig_fuel.add_trace(
                        go.Bar(
                            x=fuel_plot["hour_ending"],
                            y=fuel_plot[col],
                            name=col.replace("_", " "),
                            marker_color=palette.get(col, "#cbd5e0"),
                        )
                    )
                fig_fuel.update_layout(
                    barmode="stack",
                    height=280,
                    margin=dict(l=8, r=8, t=8, b=8),
                    legend=dict(orientation="h", y=1.16),
                )
                st.plotly_chart(fig_fuel, use_container_width=True)
            else:
                st.caption("Fuel mix unavailable for this day.")
        with right:
            st.markdown("**Congestion score**")
            if scored.empty:
                st.caption("No EIA demand — congestion score unavailable.")
            else:
                scored_plot = scored.sort_values("hour_ending")
                fig_s = go.Figure()
                _add_jam_bands(fig_s, scored_plot, 0.28)
                fig_s.add_trace(
                    go.Scatter(
                        x=scored_plot["hour_ending"],
                        y=scored_plot["congestion_score"],
                        name="BA congestion score",
                        fill="tozeroy",
                        line=dict(color="#fc8181", width=2),
                    )
                )
                fig_s.update_layout(
                    height=280,
                    margin=dict(l=8, r=8, t=8, b=8),
                    xaxis_title="Hour ending",
                    yaxis_title="Score",
                    showlegend=False,
                )
                st.plotly_chart(fig_s, use_container_width=True)
                st.caption(f"Red bands = evening jam ({jam_txt}).")

    # --- Supporting: meters ---
    with st.expander("Your meters (Millcreek)", expanded=False):
        st.caption(
            "Real RMP Green Button: main house + casita. "
            "Illustrative home shift off jam hours — fleet optimizer is §4."
        )
        meters = load_house_casita_best(date_s)
        meter_date = str(meters.get("meter_date") or date_s)
        if meters.get("is_proxy"):
            st.caption(
                f"No meter file for {date_s} — showing latest metered day ≤ target: "
                f"**{meter_date}**."
            )
        jam = set(jam_list) if jam_list else set(range(18, 23))
        m1, m2 = st.columns(2)
        with m1:
            if meters["main_house"].empty:
                st.info("No main-house intervals available.")
            else:
                st.plotly_chart(
                    _meter_chart(
                        propose_house_shift(meters["main_house"], jam),
                        f"Main house · {meter_date}",
                    ),
                    use_container_width=True,
                )
        with m2:
            if meters["casita"].empty:
                st.info("No casita intervals available.")
            else:
                st.plotly_chart(
                    _meter_chart(
                        propose_house_shift(meters["casita"], jam),
                        f"Casita · {meter_date}",
                    ),
                    use_container_width=True,
                )




if __name__ == "__main__":
    main()
