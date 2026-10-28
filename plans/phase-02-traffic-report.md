Suggested Plan                                                                  │
 │                                                                                 │
 │ Wasatch residential demand + jam response                                       │
 │                                                                                 │
 │ Decisions locked                                                                │
 │                                                                                 │
 │ • Demand: Sum the 5k synthetic homes for one calendar day (24 hourly bins),     │
 │   then multiply by 170 (≈ 850_000 / 5_000) for a labeled Wasatch Front          │
 │   residential estimate (Salt Lake + Utah + Davis + Weber).                      │
 │ • Response: Option A — congestion score + shift flexible residential kWh        │
 │   off jammed hours (not plant-level dispatch).                                  │
 │ • Utah-truth data: EIA PACE, Electricity Maps US-NW-PACE, Open-Meteo            │
 │   (Wasatch). CAISO/ERCOT stay out of the home path (comparison only if          │
 │   needed later).                                                                │
 │                                                                                 │
 │ mermaid flowchart                                                               │
 │                                                                                 │
 │ flowchart LR                                                                    │
 │   synth["housing_universe Parquet 5k homes"] --> agg["sum hourly kWh +          │
 │ flexible_kwh"]                                                                  │
 │   agg --> scale["x170 Wasatch estimate"]                                        │
 │   eia["EIA PACE demand"] --> score["congestion score v0"]                       │
 │   em["Electricity Maps US-NW-PACE"] --> score                                   │
 │   wx["Open-Meteo Millcreek"] --> score                                          │
 │   scale --> agent["shift flexible_kwh off jammed hours"]                        │
 │   score --> agent                                                               │
 │   agent --> out["before/after MW + carbon/congestion avoided"]                  │
 │                                                                                 │
 │ Layer 1 — Aggregate + scale (your demand curve)                                 │
 │                                                                                 │
 │ Script on top of existing scripts/rmp/synthesize_housing_universe.py            │
 │ (scripts/rmp/synthesize_housing_universe.py) output in                          │
 │ data/processed/housing_universe/latest/:                                        │
 │ 1. Pick a demo date present in the Parquet (or CLI --date).                     │
 │ 2. Group by hour_ending (or hour_label): sum(kwh), sum(flexible_kwh), and       │
 │    appliance splits if useful.                                                  │
 │ 3. Apply SCALE = 170 → wasatch_kwh, wasatch_flexible_kwh (and MW = kWh for      │
 │    1h intervals).                                                               │
 │ 4. Write a small tidy artifact:                                                 │
 │    data/processed/wasatch_residential_day/<date>.parquet + meta.json noting     │
 │    n_homes=5000, scale=170, implied_stock≈850000, labeled estimate.             │
 │                                                                                 │
 │ Provenance line for judges: 5k perturbed RMP-seeded homes × 170 ≈ Wasatch       │
 │ Front (SL+Utah+Davis+Weber) residential stock proxy.                            │
 │                                                                                 │
 │ Layer 2 — Grid context that “addresses” those needs (Option A)                  │
 │                                                                                 │
 │ Wire only the three Utah-relevant sources (aligns with plans/phase-01-data.md   │
 │ (plans/phase-01-data.md) / plans/research-bounties.md                           │
 │ (plans/research-bounties.md)):                                                  │
 │                                                                                 │
 │ ┌─────────────┬────────────────────────────┬────────────────────────────────┐   │
 │ │ Source      │ Pull                       │ Role vs your curve             │   │
 │ ├─────────────┼────────────────────────────┼────────────────────────────────┤   │
 │ │ EIA BA PACE │ Hourly system demand (same │ Shows system jam; residential  │   │
 │ │             │ day or next-day forecast   │ curve is the demand-side slice │   │
 │ │             │ if available)              │ we can move                    │   │
 │ ├─────────────┼────────────────────────────┼────────────────────────────────┤   │
 │ │ Electricity │ Hourly carbon intensity /  │ Labels “how clean” while load  │   │
 │ │ Maps        │ renewables share           │ is met (average CI; label ≠    │   │
 │ │ US-NW-PACE  │                            │ marginal)                      │   │
 │ ├─────────────┼────────────────────────────┼────────────────────────────────┤   │
 │ │ Open-Meteo  │ Temp + shortwave radiation │ Explains evening HVAC rush /   │   │
 │ │ Millcreek   │                            │ solar fade                     │   │
 │ └─────────────┴────────────────────────────┴────────────────────────────────┘   │
 │                                                                                 │
 │ Congestion score v0 (document in meta/README): combine e.g. PACE demand         │
 │ percentile + evening-ramp flag + carbon-intensity percentile (weights fixed     │
 │ and labeled). High score = “stay out of traffic.”                               │
 │                                                                                 │
 │ Layer 3 — Manage congestion (the answer to the bounty)                          │
 │                                                                                 │
 │ Using columns already in the synthetic data (flexible_kwh, appliance tags,      │
 │ EV):                                                                            │
 │ 1. For the scaled day profile, identify top jammed hours from the score.        │
 │ 2. Greedy / simple optimizer: move flexible kWh from high-score hours into      │
 │    low-score hours within same day (respect caps; don’t invent energy).         │
 │ 3. Report before/after: peak residential MW, MWh shifted, score-weighted        │
 │    congestion avoided, rough carbon delta using EM intensity × shifted kWh.     │
 │                                                                                 │
 │ House-level demo can still show the two real RMP meters; the ×150 curve is      │
 │ the “fleet / county-scale” traffic board.                                       │
 │                                                                                 │
 │ Explicit non-goals                                                              │
 │                                                                                 │
 │ • No CAISO/ERCOT in the Utah truth path.                                        │
 │ • No ACS per-county split this pass (single ×150 Wasatch multiplier).           │
 │ • No claiming plant dispatch; we manage demand-side congestion.                 │
 │                                                                                 │
 │ Build order                                                                     │
 │                                                                                 │
 │ 1. Aggregate+scale script + one demo-day artifact from                          │
 │    housing_universe/latest.                                                     │
 │ 2. EIA PACE + Open-Meteo (+ Electricity Maps if key) fetch for that same        │
 │    horizon.                                                                     │
 │ 3. Congestion score v0 + before/after flex-shift on the scaled curve.           │
 │ 4. Hand JSON/Parquet to UI phase for traffic report + “how we managed           │
 │    congestion.”                                                                 │
 │                                                                                 │
 │  Saved to Users/jamesgriffdgmail.com/.cursor/plans/Wasatch demand jam           │
 │  plan-7331866a.plan.md                                          