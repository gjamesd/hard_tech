# Phase 03 — Clean-power schedule agent (proposal only)

**Goal:** Make “agents that schedule around clean power” the clear last beat of the demo — **no RMP write-back, no live device control**.

**Locked decisions**

- Agent type: **proposal / schedule**, not control.
- Levers in scope: appliance flex (HVAC, laundry, DW), EV windows, optional battery (already labeled). Plug-in solar stays optional side toggle — not the hero.
- Objective: prefer hours that are **low congestion** and **cleaner EIA mix** (already have `congestion_score` + `ci_kg_per_mwh` on scored hours).
- Out of scope: RMP API dispatch, Nest/Tesla OEM, plant dispatch, new hardware.

**Demo beat (30–45s)**

1. Tomorrow / hero day → EIA traffic + jam hours.  
2. House + casita: baseload vs flex; “After agent” already sketched.  
3. **Agent card:** plain-language schedule (3–6 bullets) + fleet before/after.  
4. **One headline metric:** evening MWh off rush (have it) + optional CI-weighted kgCO₂.

---

## Checklist

### A · Agent brain (reuse, don’t rebuild)

- [x] Confirm `appliance_aware_shift` + house `propose_house_shift` both key off **scored jam hours**, not hardcoded 18–23 only when EIA exists.
- [x] Prefer receiver hours by **low congestion then low CI** (if not already — small tweak in `flex_shift.py` sort key).
- [x] Emit a structured `AgentPlan`: list of `{asset, action, from_he, to_he, why}` for house + fleet.

### B · Streamlit “Agent schedule” section (hero)

- [x] New subsection **4 · Agent schedule (proposal)** above/beside relief chart.
- [x] Bullets e.g. “Precool / hold HVAC off HE18–21 → HE14–17”, “Delay EV finish-by to HE23”, “Laundry/DW → midday belly”.
- [x] Explicit caption: *Demo proposes windows. Production → RMP DSM / OEM / VPP. No live control.*
- [x] Keep solar/battery as sidebar optional; default story = **flex + EV**.

### C · Metrics judges can quote

- [x] Big numbers: `evening_mwh_relieved`, `peak_mw_cut`, `mwh_shifted_total` (already in `ReliefResult.metrics`).
- [x] Show **congestion_avoided** / **co2_kg_delta** if computed; if weak, fix one formula and label units.
- [x] One sentence under metrics: “How we manage congestion: retime residential flex out of the rush into cleaner hours.”

### D · Pitch glue (5 min copy)

- [x] Sidebar or footer maps brief triad: navigation · traffic report · schedule agent.
- [x] README demo path step for agent section.

### E · Explicitly skip

- [ ] RMP login / Green Button Connect write.
- [ ] Real thermostat / charger APIs.
- [ ] Re-synth or more DER models.

---

## Done when

Walking the app: pick jam day → see agent bullets → see before/after → can say out loud *how we managed congestion* with one number, without mentioning solar first.
