# Wasatch Front · grid traffic

**Torus · Rush Hour for Electrons** — Hard Tech Week hackathon.

Software that knows when PacifiCorp East (PACE) is jammed and keeps Wasatch residential flex out of the rush.

**Live demo:** [wasatch-grid-traffic.streamlit.app](https://wasatch-grid-traffic.streamlit.app) _(deploy via share.streamlit.io if 404)_

## Demo path (≈2 min)

1. Open **Summer jam · 2026-07-27** (or Shoulder day).
2. **§1 Traffic** — EIA BA demand + red jam bands + congestion score.
3. Read the headline: evening MWh off rush + *how we manage congestion*.
4. **§2 Agent** — plain-language HVAC / EV / laundry windows + before/after curve.
5. **§3 Meters** — real Millcreek main house + casita (illustrative home).
6. Optional: expand the Wasatch gut-check.

Proposal only — no live RMP or device control.

## Stack

- Streamlit + Plotly UI — [`apps/streamlit_demo/`](apps/streamlit_demo/)
- RMP Green Button scrape → synth Wasatch fleet — [`scripts/rmp/`](scripts/rmp/)
- EIA PACE demand/fuel + Open-Meteo Millcreek + HGB day-ahead demand models

## Run locally

```bash
# once: slim runtime pack (models + EIA cache + hero meters)
scripts/rmp/.venv/bin/python scripts/rmp/pack_streamlit_deploy.py

scripts/rmp/.venv/bin/pip install -r apps/streamlit_demo/requirements.txt
scripts/rmp/.venv/bin/streamlit run apps/streamlit_demo/app.py
```

Optional: set `EIA_API_KEY` in repo-root `.env` (see `.env.example`). Hero days ship with cached EIA.

### Docker

```bash
scripts/rmp/.venv/bin/python scripts/rmp/pack_streamlit_deploy.py
cd apps/streamlit_demo && docker compose up --build
# http://localhost:8501
```

## Publish (Streamlit Community Cloud)

1. Push this repo (includes `data/deploy/` slim pack — no secrets).
2. [share.streamlit.io](https://share.streamlit.io) → New app
3. Main file: `apps/streamlit_demo/app.py`
4. Python requirements: `apps/streamlit_demo/requirements.txt`
5. Optional secrets: `EIA_API_KEY`
6. Paste the public URL into this README’s **Live demo** line

Mobile: sidebar starts collapsed; use the Demo day buttons on a phone.

## Brief

See [`brief/brief.md`](brief/brief.md) · plan [`plans/00-general.md`](plans/00-general.md).
