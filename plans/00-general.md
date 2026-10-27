# General Plan — Torus Opt 1: Rush Hour for Electrons

**Bounty:** Torus · $5k · Energy × AI Infrastructure  
**Research:** `plans/research-bounties.md`  
**Judging:** Wed Oct 28, 5–6 PM · creativity, insight, execution  
**Demo path (one sentence):** Show tomorrow's PACE grid "traffic," then an agent that shifts flexible kWh from the main house + casita off jammed hours and reports congestion/carbon avoided.

## Phases

| # | Phase | Blocking? | Can run in parallel with | Plan file | Status |
|---|-------|-----------|--------------------------|-----------|--------|
| 01 | Data ingest + congestion score | **Yes** | — | `phase-01-data.md` | next |
| 02 | Scheduler / agent (stay out of traffic) | Yes after 01 | 03 | `phase-02-scheduler.md` | |
| 03 | Demo UI (traffic report + before/after) | Yes after 01 | 02 | `phase-03-ui.md` | |
| 04 | Last mile: audit, persona, polish, live URL | — | — | — | |

## Architecture (thin)

```
Green Button (house+casita) ──► flexible vs baseload profile
                                         │
EIA PACE + Electricity Maps + Open-Meteo ► congestion score (next 24–48h)
                                         │
                              agent / optimizer
                                         │
                         before/after + "how we managed congestion"
```

## Requirements from the brief

- Build software that knows when the grid is jammed and stays out of traffic
- Answer: **How did you manage the grid's congestion?**
- Suggested shapes: navigation for electricity · traffic report for tomorrow · agents that schedule around clean power
- Free / open-source data only; simulated data OK if **labeled**
- Judged on creativity, clarity of insight, execution (not grid-expert perfection)

## Cut on purpose

- SMOTE / synthetic house expansion
- Floods, schools, big-box world simulation
- Full CAISO/ERCOT market participant complexity (optional labeled comparison only)
- True LMP congestion modeling / power-flow
- Controlling real appliances (recommend schedule only)
- Nucleus pitch / dual-bounty split

## Utah / Torus narrative

- Real RMP meters in Millcreek (PacifiCorp / PACE territory)
- Torus ↔ Rocky Mountain Power demand-response partnership is the sponsor mirror
- Product framing: demand-side navigation that keeps flexible load out of the rush hour Torus hardware also fights

## Next session

Deep-plan and build **phase 01** only (`phase-01-data.md`): parse Green Button → tidy CSV; wire EIA + Open-Meteo (+ Electricity Maps if key); define congestion score v0; seed one demo day.
