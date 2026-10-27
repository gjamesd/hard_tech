# Bounty Research — Hard Tech Week Hackathon

**Event:** ~48 hours (Mon kickoff → Wed 5 PM judging)  
**Background:** James Griffin-Gomez — DS/ML engineer (Python, forecasting, agents, carbon/ESG at PwC), Utah homeowner with RMP Green Button meters, fast shipper in Cursor/Angular+Firebase stack from this repo template.  
**Researched:** 2026-10-26

---

## Ranked recommendation

| Rank | Bounty | Fit | Why |
|------|--------|-----|-----|
| **1** | **Torus Opt 1 — Rush Hour for Electrons** ($5k) | Best | Forecast + multi-source data + agent schedule; RMP house data as the "vehicle"; Torus ↔ RMP demand-response is the sponsor story |
| 2 | Torus Opt 2 — Compute Follows the Sun ($5k) | Strong | Same stack; weaker without real cluster traces |
| 3 | Nucleus open call ($10k) | Avoid (for us) | Rubric wants hard-tech depth + path-to-reality; no prior hardware/IP → soft odds vs real prototypes |

**Decision:** Ship Torus Option 1.

---

## Bounty A — Torus Option 1: Rush Hour for Electrons

**Prize:** $5,000  
**Prompt:** Build software that knows when the grid is jammed and stays out of traffic.  
**Judging (from brief):** creativity, clarity of insight, execution. Free/open data only; labeled simulated data OK. They do **not** expect grid experts.

### 1. Sponsor

| | |
|--|--|
| **What they sell** | Hybrid flywheel + battery "modular power plants" + software/security — behind-the-meter storage that responds in milliseconds |
| **Customers** | Utilities (esp. Rocky Mountain Power / PacifiCorp Wattsmart) and C&I (Snowbird, SLCC, SLC Airport, National Vision, industrial) |
| **Utah hook** | HQ / GigaOne in SLC; **500 MW** flexible DR capacity partnership with RMP; 359 VPP events in 2025 |
| **Why this bounty** | Torus wins when flexible load + storage **avoid peaks / stabilize frequency**. Software that *sees* congestion and shifts load is the demand-side twin of their hardware story |

Refs: [torus.co](https://www.torus.co/), [RMP case study](https://www.torus.co/case-studies/case-studies-rocky-mountain-power)

### 2. What already exists

| Type | Examples | Takeaway |
|------|----------|----------|
| Carbon-aware schedulers | BBC [carbon-minimiser](https://github.com/bbc/carbon-minimiser), [snag](https://github.com/awjlogan/snag) | UK-only; prove the *pattern* (find clean window for duration D) |
| Household flex optimizers | [community-energy-flex](https://github.com/rosscyking1115/community-energy-flex) | LP + carbon + tariff; label live vs sample |
| Carbon APIs | Electricity Maps, WattTime (MOER), Singularity | Don't rebuild the map — sit *on top* |
| DR / HEMS | OhmConnect, Leap, Sense, utility TOU apps | Commercial; we need a sharp 48h insight, not a utility product |
| Prior hackathons | GridShift (CAISO + WattTime), Base Power GridMarket (ERCOT) | Live ISO data + one clear decision |

**Differentiation for us:** Real Millcreek Green Button (house + casita) × PACE/Utah traffic report × agent that moves flexible kWh off the jam — tied to Torus/RMP narrative.

### 3. Data

| Source | Role | Public API? | Auth | Limits / notes | Docs |
|--------|------|-------------|------|----------------|------|
| **EIA Open Data** | BA load / demand (PACE) | Yes | Free `api_key` | 5k rows/request; paginate | [docs](https://www.eia.gov/opendata/documentation.php) · [register](https://www.eia.gov/opendata/register.php) |
| **Electricity Maps** | Carbon intensity, renewables, load, forecasts by zone (`US-NW-PACE`) | Yes | `auth-token` header; free trial | Plan-scoped zones/quotas; 429 | [API docs](https://app.electricitymaps.com/api/docs/getting-started) |
| **Open-Meteo** | Weather / solar irradiance proxy for Millcreek | Yes | **None** | ~10k calls/day free, attribution | [docs](https://open-meteo.com/en/docs) |
| **CAISO OASIS** | "Known jam" market: load forecast, prices | Yes (anonymous) | None for OASIS download | ZIP/CSV; quirky query params | [OASIS PDF](https://www.caiso.com/Documents/OASISAPISpecification.pdf) |
| **ERCOT Public API** | Alternate jam market | Yes | Register + subscription key + 1h ID token | Heavier auth | [ERCOT auth](https://developer.ercot.com/applications/pubapi/user-guide/registration-and-authentication/) |
| **RMP Green Button** | Our meters (vehicle layer) | Download, not API | Account login | ESPI XML; scrape would be fragile — **manual export** | Portal: csapps.rockymountainpower.net |
| **WattTime** (optional) | Marginal emissions (MOER) | Yes | Account; free CAISO_NORTH only | Utah likely paid | [docs.watttime.org](https://docs.watttime.org/) |

**Scraping flags:** RMP portal (avoid — use Green Button download). CAISO web UI (avoid — use OASIS). Don't scrape Electricity Maps map tiles.

**Utah zone truth:** PacifiCorp East balancing authority = **PACE**; Electricity Maps zone **`US-NW-PACE`**. Wasatch Front is the PACE load center.

### 4. The hard part

Not charts. Not CRUD.

**A defensible congestion score for the next 24–48h + a scheduler that moves flexible residential kWh off jammed hours with a measurable before/after** — answering: *How did you manage the grid's congestion?*

A search box / dashboard of Electricity Maps loses. An optimizer with labeled assumptions and a Torus-relevant narrative wins.

### 5. Domain knowledge (avoid being subtly wrong)

- **Average ≠ marginal carbon.** Shifting load cares about what generator turns on/off (marginal). Electricity Maps average intensity is OK if labeled; WattTime MOER is cleaner but harder for Utah on free tier.
- **Utah ≠ CAISO.** Use PACE for home truth; use CAISO/ERCOT as labeled comparison markets if needed.
- **Congestion ≠ high usage at your house.** Your midday spike is local load; grid jam is system peak / high price / dirty mix / net-load ramp (often evening when solar fades).
- **Not all kWh are flexible.** Baseload (fridge, always-on) stays; laundry/EV/dishwasher can shift.
- **TOU vs wholesale.** RMP residential rates ≠ LMP. Demo "congestion exposure" / carbon / relative cost — don't invent fake $/kWh savings.
- **Simulated data must be labeled** (brief rule).

---

## Bounty B — Torus Option 2: Compute Follows the Sun

**Prize:** $5,000  
**Prompt:** Decide where/when compute should run for cheapest/cleanest power.

### 1. Sponsor fit
Same Torus. Aligns with data-center / flexible compute load + their utility partnerships. Slightly more "AI infra" framed.

### 2. Exists
Carbon-aware K8s schedulers, cloud carbon APIs, BBC minimiser for "which region," training-job deferral tools. Crowded idea; needs real or well-labeled cluster traces.

### 3. Data
EIA, NREL, NASA POWER, Open-Meteo, cluster traces (usually **synthetic/labeled** in 48h). Same API auth pattern as Opt 1.

### 4. Hard part
Credible **multi-site routing** with carbon saved — not a heatmap of sunny regions.

### 5. Domain gotchas
Latency/data gravity; carbon of networking; spot price ≠ carbon; fake GPU traces fool nobody unless labeled.

**Fit:** Strong stack match, weaker data story than Opt 1 with our Green Button.

---

## Bounty C — Nucleus × Startup State open call

**Prize:** $10,000  
**Judges:** Angela Smith, Tyler Jennings, Cam Dionne, Dylan Doty  
**Rubric:** Progress beyond concept 30% · Path to reality 25% · Hard tech depth 20% · Utah impact 15% · Team/pitch 10%

### 1. Sponsor
Nucleus Institute + Startup State grow Utah hard tech: Nucleus Fund (~$40M deep tech), Nucleus Grow (SBIR/STTR), UTIF microgrants/loans. Bounty is a **deal-flow / prototype funnel**, not a product feature request.

### 2. Exists
Whatever teams bring — sensors, devices, materials, energy hardware, etc.

### 3. Data
None provided. Bring your own. No sensitive/export-controlled/NDA IP.

### 4. Hard part
**Concrete progress this week** + credible next milestone for the bounty dollars — not a pitch deck alone.

### 5. Domain
Hard tech = real engineering/science challenge. Pure software DS/ML without prior hardware is easy to score low on "hard tech depth."

**Fit:** Avoid unless we had a pre-existing hard-tech prototype.

---

## Background → scorecard

| Criterion (1–5) | Torus Opt 1 | Torus Opt 2 | Nucleus |
|-----------------|-------------|-------------|---------|
| Domain feel | 4 (ESG/carbon + Utah home) | 4 | 2 |
| Stack fit | 5 | 5 | 3 |
| Data exists | 4 | 3 | n/a |
| Hard part clear | 5 | 4 | 2 |
| Rubric ↔ strengths | 5 | 4 | 2 |
| **Total** | **23** | **20** | **~9** |

---

## Immediate actions from research

1. Register **EIA** API key; start **Electricity Maps** free trial; confirm `US-NW-PACE`.
2. Export **multi-week** Green Button for **both** RMP meters (no SMOTE).
3. Ask Torus on-site if they have Electricity Maps / partner keys.
4. Lock demo sentence: *We manage congestion by shifting flexible residential load off PACE rush-hour windows.*
