# Phase 01 — Data ingest + congestion score

**Goal:** Trusted inputs for the demo — house load + tomorrow's grid traffic signal.  
**Brief requirements covered:** "knows when the grid is jammed"; data from EIA / Electricity Maps / Open-Meteo; Green Button as vehicle layer.  
**Depends on:** Research decision (Torus Opt 1). Blocks phases 02–03.

## Checklist

- [ ] Export multi-day/week Green Button XML for **both** RMP meters (main + casita); store under `data/raw/` (gitignored if sensitive)
- [ ] Parser: ESPI XML → `timestamp, meter_id, kwh` CSV/Parquet (`data/processed/`)
- [ ] Register EIA API key; pull hourly demand for BA **PACE**
- [ ] Open-Meteo hourly forecast for Millcreek (temp + shortwave radiation)
- [ ] Electricity Maps trial key → carbon intensity / renewables for `US-NW-PACE` (fallback: EIA + weather only, labeled)
- [ ] Define **congestion score v0** (document formula): e.g. combine net-load percentile, carbon intensity percentile, evening ramp flag
- [ ] Seed one labeled demo horizon (next 24–48h JSON) so UI can work offline
- [ ] Shared types for reading, score, meter (`shared/` or app models)

## Done when

- [ ] Demo path step works: given a time range, return hourly congestion score + house kWh for both meters
- [ ] Formula and data provenance written in README or `brief/`
- [ ] Ready to hand score series to phase 02 scheduler
