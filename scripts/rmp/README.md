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
