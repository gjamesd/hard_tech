from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PlanStep:
    asset: str
    action: str


def _jam_hours(scored: pd.DataFrame) -> list[int]:
    if scored.empty:
        return [18, 19, 20, 21, 22]
    if "is_jam" in scored.columns:
        jam = sorted(
            scored.loc[scored["is_jam"], "hour_ending"].astype(int).tolist()
        )
        if jam:
            return jam
    if "congestion_score" in scored.columns:
        top = sorted(
            scored.nlargest(5, "congestion_score")["hour_ending"].astype(int).tolist()
        )
        if top:
            return top
    return [18, 19, 20, 21, 22]


def _jam_label(hours: list[int]) -> str:
    if not hours:
        return "HE18–22"
    if hours[-1] - hours[0] + 1 == len(hours):
        return f"HE{hours[0]}–{hours[-1]}"
    return "HE" + ",".join(str(h) for h in hours)


def _shoulder_label(jam: list[int]) -> str:
    if jam:
        start = max(12, jam[0] - 4)
        end = max(start, jam[0] - 1)
        if end >= start:
            return f"HE{start}–{end}"
    return "HE12–16"


def build_agent_plan(
    scored: pd.DataFrame,
    *,
    use_flex: bool = True,
    use_battery: bool = False,
    adoption_pct: float = 0.0,
) -> list[PlanStep]:
    jam_he = _jam_hours(scored)
    jam = _jam_label(jam_he)
    shoulder = _shoulder_label(jam_he)
    steps: list[PlanStep] = []

    if use_flex:
        steps.append(
            PlanStep(
                asset="HVAC",
                action=(
                    f"Pre-cool / hold setbacks off {jam} → shift that flex into "
                    f"{shoulder} so the house is out of evening rush."
                ),
            )
        )
        steps.append(
            PlanStep(
                asset="EV + laundry / dishwasher",
                action=(
                    f"Delay EV finish-by and wet appliances past {jam}; "
                    "prefer midday belly or after HE23."
                ),
            )
        )

    if adoption_pct > 0:
        steps.append(
            PlanStep(
                asset="SFH plug-in solar (HB 340)",
                action=(
                    f"≤1.2 kW kits on {adoption_pct:.0f}% of SFH cut daytime grid draw. "
                    "Apartments never get kits."
                ),
            )
        )

    if use_battery and adoption_pct > 0:
        steps.append(
            PlanStep(
                asset="SFH battery",
                action=(
                    f"Store ~2 kWh/home·day surplus at noon; discharge into {jam} "
                    "(and a light HE3–5 top-up). Charge MWh = discharge MWh."
                ),
            )
        )

    if use_flex and len(steps) < 5:
        steps.append(
            PlanStep(
                asset="Renters / apartments",
                action=(
                    f"~25% of Wasatch can’t hang a kit — same flex windows off {jam}, "
                    "milder peak so apartment load doesn’t sync into one valley."
                ),
            )
        )

    if not steps:
        steps.append(
            PlanStep(
                asset="Levers off",
                action=(
                    "Turn on Appliance + EV flex-shift and/or raise solar adoption "
                    "to see the agent move load."
                ),
            )
        )

    if len(steps) < 5:
        steps.append(
            PlanStep(
                asset="How it ships",
                action=(
                    "Proposal only — publish windows to RMP DSM / OEM / VPP opt-in. "
                    "No live breaker control."
                ),
            )
        )
    return steps[:5]
