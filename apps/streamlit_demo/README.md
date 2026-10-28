# Wasatch Front · grid traffic (Streamlit)

Brief demo path:

1. **Traffic** — EIA PACE jam bands + congestion score
2. **Agent schedule (proposal)** — HVAC/EV/laundry windows; before/after; evening MWh off rush
3. **Meters** — real RMP main house + casita
4. **Wasatch detail** — residential proxy (+ gut-check expander)

## Setup

```bash
# from repo root
scripts/rmp/.venv/bin/pip install -r apps/streamlit_demo/requirements.txt
scripts/rmp/.venv/bin/python scripts/rmp/pack_streamlit_deploy.py

# full rebuild (optional, if pack sources missing)
scripts/rmp/.venv/bin/python scripts/rmp/aggregate_wasatch_day.py --all-dates
scripts/rmp/.venv/bin/python scripts/rmp/build_demo_day_pack.py --heroes
scripts/rmp/.venv/bin/python scripts/rmp/train_demand_forecast.py
scripts/rmp/.venv/bin/python scripts/rmp/pack_streamlit_deploy.py

scripts/rmp/.venv/bin/streamlit run apps/streamlit_demo/app.py
```

Optional: `EIA_API_KEY` in repo `.env` for live PACE pulls. Cached hero days work offline.

## Docker

```bash
# from repo root — image bakes data/deploy
scripts/rmp/.venv/bin/python scripts/rmp/pack_streamlit_deploy.py
cd apps/streamlit_demo
docker compose up --build
# http://localhost:8501
```

## Streamlit Community Cloud

| Setting | Value |
|---------|--------|
| Main file path | `apps/streamlit_demo/app.py` |
| Requirements | `apps/streamlit_demo/requirements.txt` |
| Secrets (optional) | `EIA_API_KEY` |

Runtime data is `data/deploy/` (tracked). `lib/paths.py` falls back to it when local `data/processed` is absent.

Mobile: sidebar collapsed by default; Demo day chips for summer jam / shoulder.

## Models

Packed under `data/deploy/models/` (and local `models/`):

- `wasatch_demand_hgb.pkl`
- `pace_demand_hgb.pkl`
- `model_meta.json`

Features: lags + hour/dow + Open-Meteo Millcreek (temp, shortwave, precip, snowfall).
