# Hard Tech Week Hackathon — Working Context

**Event:** Hard Tech Week Hackathon · Oct 26–28, 2026 · Aernata, SLC  
**Chosen bounty:** Torus · Option 1 — Rush Hour for Electrons · **$5,000**  
**Judging:** Wed Oct 28, 5–6 PM · creativity, clarity of insight, execution  
**Research:** `plans/research-bounties.md` · **Plan:** `plans/00-general.md`

## Challenge (verbatim spirit)

The grid has a rush hour: demand spikes, prices jump, solar fades. Most software ignores it.

Build software that knows when the grid is jammed and stays out of traffic.

- a navigation app for electricity
- a traffic report for tomorrow's grid
- agents that schedule around clean power

**How would you manage the grid's congestion?**

## Data (brief + ours)

| Source | Use |
|--------|-----|
| EIA | PACE balancing-authority load |
| Electricity Maps | Carbon / mix for `US-NW-PACE` |
| Open-Meteo | Weather / solar context (Millcreek) |
| CAISO / ERCOT | Optional labeled "known jam" markets |
| RMP Green Button | Real house + casita hourly kWh (vehicle layer) |

Free/open data only. Simulated data OK if labeled.

## Demo path

1. Show tomorrow's grid traffic for PACE / Millcreek (congestion score).
2. Show main house + casita load; mark flexible vs baseload.
3. Agent proposes shifted schedule that avoids jammed hours.
4. One metric: kWh / carbon / congestion exposure moved off the rush hour.

## Cut on purpose

SMOTE, floods/schools world-model, power-flow physics, live appliance control, Nucleus dual-track.
