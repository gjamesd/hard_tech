# Rocky Mountain Power hourly scrape

Playwright UI scrape — portal encrypts interval requests, so we drive the browser
and capture `getIntervalUsageForDate` JSON.

## Keep

| File | Role |
|------|------|
| `smoke_and_backfill_hourly.py` | Login + day-by-day hourly pull (both meters) |
| `rate_limit.py` | Polite delays / backoff |

Credentials live in the **repo-root** `.env` (never commit it):

```
RMP_USERNAME=...
RMP_PASSWORD=...
RMP_START_DATE=2026-02-01
RMP_MIN_DELAY_SEC=30
RMP_MAX_DELAY_SEC=40
```

## Setup

```bash
cd scripts/rmp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
# Both meters from RMP_START_DATE
python smoke_and_backfill_hourly.py

# One meter
python smoke_and_backfill_hourly.py --only-meter 50752799_casita
python smoke_and_backfill_hourly.py --only-meter 342856918_sfh
```

Output (gitignored): `data/raw/rmp/hourly/<meter_key>/<YYYY-MM-DD>.json`

Meter folders use serial + role labels only (no street address).

## Layer 2 — synthetic housing universe

Household diversity perturbation on the two seed meters:

- size multiplier $\alpha_i \sim \mathrm{LogNormal}(0, 0.25)$
- schedule phase $\Delta t_i \in \{-2,-1,0,+1,+2\}$ hours
- appliance flex tags: HVAC + laundry + dishwasher on every home; ~25% of SFH also get an EV charger block

```bash
# Smoke (15 homes × 7 days)
python synthesize_housing_universe.py --smoke

# Full Wasatch sample: 3,750 SFH + 1,250 apartments
python synthesize_housing_universe.py

# After IAM can write S3:
python synthesize_housing_universe.py --upload-s3 --bucket hard-tech-bucket
```

Writes gitignored Parquet to `data/processed/housing_universe/`.

## Layer 3 — Wasatch aggregate (stock ×180 × intensity 1.35) + Streamlit

```bash
# Aggregate 5k homes → Wasatch MW (stock ≈ four counties, intensity for light seed)
python aggregate_wasatch_day.py --all-dates

# Build hero day packs (EIA PACE if EIA_API_KEY set; else labeled synthetic proxy)
python build_demo_day_pack.py --heroes

# Train tomorrow's Wasatch + PACE demand pickles (Open-Meteo weather features)
python train_demand_forecast.py

# Slim pack for Streamlit Cloud / Docker (~8 MB)
python pack_streamlit_deploy.py

# UI
cd ../../apps/streamlit_demo && streamlit run app.py
# or: docker compose up --build
```

See [`apps/streamlit_demo/README.md`](../../apps/streamlit_demo/README.md).
