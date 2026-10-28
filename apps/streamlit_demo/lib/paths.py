from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ.get("GRID_RUSH_ROOT", str(_REPO))).resolve()


def _data_root() -> Path:
    """Prefer full local tree; fall back to slim data/deploy for Cloud/Docker."""
    force = os.environ.get("GRID_RUSH_USE_DEPLOY", "").strip() in {"1", "true", "yes"}
    deploy = ROOT / "data" / "deploy"
    local_wasatch = (
        ROOT / "data" / "processed" / "wasatch_residential_day" / "all_days.parquet"
    )
    deploy_wasatch = (
        deploy / "processed" / "wasatch_residential_day" / "all_days.parquet"
    )
    if force and deploy_wasatch.is_file():
        return deploy
    if local_wasatch.is_file():
        return ROOT / "data"
    if deploy_wasatch.is_file():
        return deploy
    return ROOT / "data"


DATA_ROOT = _data_root()
HOUSING_LATEST = DATA_ROOT / "processed" / "housing_universe" / "latest"
WASATCH_DAY_DIR = DATA_ROOT / "processed" / "wasatch_residential_day"
GRID_CACHE_DIR = DATA_ROOT / "processed" / "grid_pace"
DEMO_PACK_DIR = DATA_ROOT / "processed" / "demo_day_pack"
RAW_HOURLY_DIR = DATA_ROOT / "raw" / "rmp" / "hourly"

SCALE = 180
N_SAMPLE_HOMES = 5000
IMPLIED_STOCK = N_SAMPLE_HOMES * SCALE
WASATCH_COUNTIES = ("Salt Lake", "Utah", "Davis", "Weber")

INTENSITY = 1.0
EFFECTIVE_MULT = SCALE * INTENSITY

MILLCREEK_LAT = 40.6997
MILLCREEK_LON = -111.8142

HERO_DATES = ("2026-07-27", "2026-10-25")
HERO_LABELS = {
    "2026-07-27": "Summer jam",
    "2026-10-25": "Shoulder day",
}


def _models_dir() -> Path:
    local = ROOT / "models"
    deploy = ROOT / "data" / "deploy" / "models"
    force = os.environ.get("GRID_RUSH_USE_DEPLOY", "").strip() in {"1", "true", "yes"}
    if force and (deploy / "wasatch_demand_hgb.pkl").is_file():
        return deploy
    if (local / "wasatch_demand_hgb.pkl").is_file():
        return local
    if (deploy / "wasatch_demand_hgb.pkl").is_file():
        return deploy
    return local


MODELS_DIR = _models_dir()
WASATCH_MODEL_PATH = MODELS_DIR / "wasatch_demand_hgb.pkl"
PACE_MODEL_PATH = MODELS_DIR / "pace_demand_hgb.pkl"
MODEL_META_PATH = MODELS_DIR / "model_meta.json"
