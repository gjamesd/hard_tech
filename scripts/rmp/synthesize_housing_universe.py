#!/usr/bin/env python3
"""Layer 2 — Wasatch Front housing universe from two RMP seed meters.

Builds a synthetic residential sample:
  - 3,750 SFH / townhomes (75%) seeded from 342856918_sfh
  - 1,250 apartments (25%) seeded from 50752799_casita

Household diversity perturbation:
  - size multiplier α_i ~ LogNormal(0, 0.25)
  - schedule phase Δt_i ∈ {-2,-1,0,+1,+2} hours
  - appliance flex tags: HVAC + laundry + dishwasher on every home;
    ~25% of SFH also get an EV charger block

Writes Parquet under data/processed/housing_universe/ and optionally syncs to S3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

RAW_HOURLY = ROOT / "data" / "raw" / "rmp" / "hourly"
OUT_DIR = ROOT / "data" / "processed" / "housing_universe"

SEED_SFH = "342856918_sfh"
SEED_APT = "50752799_casita"

HOURS = [f"{h:02d}:00" for h in range(1, 25)]
PHASE_SHIFTS = (-2, -1, 0, 1, 2)
ALPHA_SIGMA = 0.25


@dataclass(frozen=True)
class Archetype:
    name: str
    seed_key: str
    n_homes: int
    ev_penetration: float
    hvac_flex: tuple[float, float]
    laundry_flex: tuple[float, float]
    dishwasher_flex: tuple[float, float]


ARCHETYPES = (
    Archetype(
        name="sfh",
        seed_key=SEED_SFH,
        n_homes=3750,
        ev_penetration=0.25,
        hvac_flex=(0.18, 0.24),
        laundry_flex=(0.06, 0.10),
        dishwasher_flex=(0.04, 0.08),
    ),
    Archetype(
        name="apartment",
        seed_key=SEED_APT,
        n_homes=1250,
        ev_penetration=0.0,
        hvac_flex=(0.08, 0.12),
        laundry_flex=(0.03, 0.05),
        dishwasher_flex=(0.02, 0.04),
    ),
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Synthesize Wasatch Front residential load")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--smoke", action="store_true", help="10 SFH + 5 apt, 7 newest days")
    p.add_argument("--n-sfh", type=int, default=None)
    p.add_argument("--n-apt", type=int, default=None)
    p.add_argument("--max-days", type=int, default=None)
    p.add_argument("--upload-s3", action="store_true")
    p.add_argument("--bucket", default=os.getenv("RMP_S3_BUCKET", "hard-tech-bucket"))
    p.add_argument("--s3-prefix", default="synthetic/wasatch_5k")
    return p.parse_args()


def load_seed_days(seed_key: str) -> dict[date, np.ndarray]:
    folder = RAW_HOURLY / seed_key
    if not folder.is_dir():
        sys.exit(f"Missing seed folder: {folder}")
    out: dict[date, np.ndarray] = {}
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        payload = json.loads(path.read_text())
        body = payload.get("getIntervalUsageForDateResponseBody") or {}
        rows = (body.get("response") or {}).get("intervalDataResponse") or []
        if not isinstance(rows, list) or len(rows) < 20:
            continue
        by_hour = {str(r.get("readTime")): float(r.get("usage") or 0.0) for r in rows}
        vec = np.array([by_hour.get(h, 0.0) for h in HOURS], dtype=np.float64)
        if vec.sum() <= 0:
            continue
        out[day] = vec
    if not out:
        sys.exit(f"No usable days in {folder}")
    return out


def evening_mask() -> np.ndarray:
    # 18:00–22:00 inclusive on RMP 01:00..24:00 labels → indices 17..21
    m = np.zeros(24, dtype=np.float64)
    m[17:22] = 1.0
    return m


def _norm_mask(m: np.ndarray) -> np.ndarray:
    total = float(m.sum())
    if total <= 0:
        return m
    return m / total


def appliance_masks(archetype: str) -> dict[str, np.ndarray]:
    """Archetype-appropriate hour weights for shiftable end uses."""
    hvac = np.zeros(24, dtype=np.float64)
    laundry = np.zeros(24, dtype=np.float64)
    dishwasher = np.zeros(24, dtype=np.float64)
    if archetype == "sfh":
        hvac[13:22] = 1.0
        laundry[10:14] = 1.0
        laundry[19:21] = 0.6
        dishwasher[19:23] = 1.0
    else:
        hvac[17:22] = 1.0
        laundry[9:12] = 1.0
        dishwasher[20:23] = 1.0
    return {
        "hvac": _norm_mask(hvac),
        "laundry": _norm_mask(laundry),
        "dishwasher": _norm_mask(dishwasher),
    }


def ev_profile(rng: np.random.Generator) -> np.ndarray:
    """Level-2 style block (~7 kWh) either overnight or after-work."""
    profile = np.zeros(24, dtype=np.float64)
    mode = rng.choice(["overnight", "evening"], p=[0.55, 0.45])
    total = float(rng.uniform(5.5, 9.0))
    if mode == "overnight":
        start = int(rng.integers(0, 4))
        hours = int(rng.integers(3, 5))
    else:
        start = int(rng.integers(18, 21))
        hours = int(rng.integers(2, 4))
    idx = [(start + i) % 24 for i in range(hours)]
    weights = rng.dirichlet(np.ones(hours))
    for i, h in enumerate(idx):
        profile[h] += total * float(weights[i])
    return profile


def build_home_params(
    arch: Archetype, home_index: int, rng: np.random.Generator
) -> dict:
    has_ev = bool(rng.random() < arch.ev_penetration)
    hvac_frac = float(rng.uniform(*arch.hvac_flex))
    laundry_frac = float(rng.uniform(*arch.laundry_flex))
    dishwasher_frac = float(rng.uniform(*arch.dishwasher_flex))
    return {
        "home_id": f"{arch.name}_{home_index:04d}",
        "archetype": arch.name,
        "seed_meter": arch.seed_key,
        "alpha": float(rng.lognormal(mean=0.0, sigma=ALPHA_SIGMA)),
        "phase_shift": int(rng.choice(PHASE_SHIFTS)),
        "has_hvac_flex": True,
        "has_laundry_flex": True,
        "has_dishwasher_flex": True,
        "has_ev": has_ev,
        "hvac_frac": hvac_frac,
        "laundry_frac": laundry_frac,
        "dishwasher_frac": dishwasher_frac,
        "flex_frac": hvac_frac + laundry_frac + dishwasher_frac,
        "ev_profile": ev_profile(rng) if has_ev else np.zeros(24),
        "masks": appliance_masks(arch.name),
    }


def _allocate_flex(
    kwh: np.ndarray, frac: float, mask: np.ndarray
) -> np.ndarray:
    daily = float(kwh.sum())
    if daily <= 0 or frac <= 0:
        return np.zeros(24, dtype=np.float64)
    return daily * frac * mask


def perturb_day(
    seed_vec: np.ndarray, params: dict, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    vec = seed_vec.copy()
    shift = params["phase_shift"]
    if shift:
        vec = np.roll(vec, shift)

    kwh = np.maximum(vec * params["alpha"], 0.0)
    ev = params["ev_profile"] * float(rng.uniform(0.85, 1.15))
    kwh = kwh + ev

    masks = params["masks"]
    flex_hvac = _allocate_flex(kwh, params["hvac_frac"], masks["hvac"])
    flex_laundry = _allocate_flex(kwh, params["laundry_frac"], masks["laundry"])
    flex_dishwasher = _allocate_flex(
        kwh, params["dishwasher_frac"], masks["dishwasher"]
    )
    flex_ev = ev.copy()

    flexible = flex_hvac + flex_laundry + flex_dishwasher + flex_ev
    cap = kwh * 0.95
    over = flexible > cap
    if np.any(over):
        scale = np.ones(24, dtype=np.float64)
        scale[over] = cap[over] / flexible[over]
        flex_hvac *= scale
        flex_laundry *= scale
        flex_dishwasher *= scale
        flex_ev *= scale
        flexible = flex_hvac + flex_laundry + flex_dishwasher + flex_ev

    return {
        "kwh": kwh,
        "flexible_kwh": flexible,
        "flex_hvac_kwh": flex_hvac,
        "flex_laundry_kwh": flex_laundry,
        "flex_dishwasher_kwh": flex_dishwasher,
        "flex_ev_kwh": flex_ev,
    }


def home_frame(
    params: dict,
    available: list[date],
    seed_days: dict[date, np.ndarray],
    rng: np.random.Generator,
    arch_name: str,
) -> pd.DataFrame:
    eve = evening_mask()
    records: list[dict] = []
    for day in available:
        loads = perturb_day(seed_days[day], params, rng)
        for hour_idx, hour_label in enumerate(HOURS):
            hour_end = int(hour_label.split(":")[0]) % 24
            records.append(
                {
                    "home_id": params["home_id"],
                    "archetype": arch_name,
                    "seed_meter": params["seed_meter"],
                    "date": day.isoformat(),
                    "hour_ending": hour_end if hour_end != 0 else 24,
                    "hour_label": hour_label,
                    "kwh": round(float(loads["kwh"][hour_idx]), 4),
                    "flexible_kwh": round(float(loads["flexible_kwh"][hour_idx]), 4),
                    "flex_hvac_kwh": round(
                        float(loads["flex_hvac_kwh"][hour_idx]), 4
                    ),
                    "flex_laundry_kwh": round(
                        float(loads["flex_laundry_kwh"][hour_idx]), 4
                    ),
                    "flex_dishwasher_kwh": round(
                        float(loads["flex_dishwasher_kwh"][hour_idx]), 4
                    ),
                    "flex_ev_kwh": round(float(loads["flex_ev_kwh"][hour_idx]), 4),
                    "alpha": round(params["alpha"], 4),
                    "phase_shift": params["phase_shift"],
                    "has_hvac_flex": True,
                    "has_laundry_flex": True,
                    "has_dishwasher_flex": True,
                    "has_ev": bool(params["has_ev"]),
                    "flex_frac": round(params["flex_frac"], 4),
                    "is_evening_peak": bool(eve[hour_idx] > 0),
                }
            )
    return pd.DataFrame.from_records(records)


def synthesize_to_dir(
    seeds: dict[str, dict[date, np.ndarray]],
    days: list[date],
    counts: dict[str, int],
    rng: np.random.Generator,
    out_dir: Path,
    batch_size: int = 50,
) -> tuple[Path, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part-*.parquet"):
        old.unlink()

    totals = {"rows": 0, "homes": 0, "ev_homes": 0, "kwh": 0.0, "flexible_kwh": 0.0}
    by_arch: dict[str, dict] = {}

    part = 0
    for arch in ARCHETYPES:
        n = counts[arch.name]
        seed_days = seeds[arch.seed_key]
        available = [d for d in days if d in seed_days]
        if not available:
            sys.exit(f"No overlapping days for seed {arch.seed_key}")
        print(f"→ {arch.name}: {n} homes × {len(available)} days")
        arch_stats = {
            "homes": 0,
            "ev_homes": 0,
            "kwh": 0.0,
            "flexible_kwh": 0.0,
            "home_days": 0,
        }
        batch: list[pd.DataFrame] = []
        for i in range(n):
            params = build_home_params(arch, i + 1, rng)
            frame = home_frame(params, available, seed_days, rng, arch.name)
            batch.append(frame)
            arch_stats["homes"] += 1
            arch_stats["ev_homes"] += int(params["has_ev"])
            arch_stats["kwh"] += float(frame["kwh"].sum())
            arch_stats["flexible_kwh"] += float(frame["flexible_kwh"].sum())
            arch_stats["home_days"] += len(available)
            if len(batch) >= batch_size or i + 1 == n:
                chunk = pd.concat(batch, ignore_index=True)
                path = out_dir / f"part-{part:04d}.parquet"
                chunk.to_parquet(path, index=False)
                totals["rows"] += len(chunk)
                part += 1
                batch = []
            if (i + 1) % 500 == 0 or i + 1 == n:
                print(f"  {arch.name} homes done: {i + 1}/{n}")
        by_arch[arch.name] = arch_stats
        totals["homes"] += arch_stats["homes"]
        totals["ev_homes"] += arch_stats["ev_homes"]
        totals["kwh"] += arch_stats["kwh"]
        totals["flexible_kwh"] += arch_stats["flexible_kwh"]

    return out_dir, {"totals": totals, "by_archetype": by_arch}


def upload_s3_dir(local_dir: Path, bucket: str, prefix: str) -> None:
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        sys.exit("boto3 missing — pip install boto3")

    s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-2"))
    try:
        for path in sorted(local_dir.iterdir()):
            if not path.is_file():
                continue
            key = f"{prefix.rstrip('/')}/{path.name}"
            print(f"Uploading s3://{bucket}/{key}")
            s3.upload_file(str(path), bucket, key)
        print("S3 upload complete")
    except ClientError as exc:
        sys.exit(
            f"S3 upload failed: {exc}\n"
            "Attach AmazonS3FullAccess (or bucket write) to hard-tech-local, then retry --upload-s3."
        )


def main() -> None:
    args = parse_args()
    rng = np.random.default_rng(args.seed)

    seeds = {
        SEED_SFH: load_seed_days(SEED_SFH),
        SEED_APT: load_seed_days(SEED_APT),
    }
    all_days = sorted(set(seeds[SEED_SFH]) & set(seeds[SEED_APT]))
    if args.max_days:
        all_days = all_days[-args.max_days :]
    if args.smoke:
        all_days = all_days[-7:]
        counts = {"sfh": 10, "apartment": 5}
    else:
        counts = {
            "sfh": args.n_sfh if args.n_sfh is not None else 3750,
            "apartment": args.n_apt if args.n_apt is not None else 1250,
        }

    print(
        f"Universe: {counts['sfh']} SFH + {counts['apartment']} apt · "
        f"{len(all_days)} days ({all_days[0]} → {all_days[-1]})"
    )

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUT_DIR / f"run_{stamp}"
    latest_dir = OUT_DIR / "latest"
    out_dir, stats = synthesize_to_dir(seeds, all_days, counts, rng, run_dir)

    meta = {
        "created_utc": datetime.utcnow().isoformat() + "Z",
        "seed": args.seed,
        "counts": counts,
        "day_start": all_days[0].isoformat(),
        "day_end": all_days[-1].isoformat(),
        "n_days": len(all_days),
        "n_rows": stats["totals"]["rows"],
        "seeds": {
            "sfh": SEED_SFH,
            "apartment": SEED_APT,
            "note": "507=casita/apartment archetype seed; 342=sfh/main-house archetype seed",
        },
        "assumptions": {
            "geography": "Wasatch Front residential stock proxy (Salt Lake + Utah + Davis/Weber)",
            "mix": "75% SFH/townhome, 25% apartment",
            "household_size_multiplier": "alpha_i ~ LogNormal(0, 0.25)",
            "schedule_phase_shift_hours": list(PHASE_SHIFTS),
            "flex_tags": "100% HVAC + laundry + dishwasher; SFH EV ~25%",
            "sfh_flex_shares": "HVAC 18-24%, laundry 6-10%, dishwasher 4-8%",
            "apt_flex_shares": "HVAC 8-12%, laundry 3-5%, dishwasher 2-4%",
            "sfh_ev_penetration": "25% Level-2 style blocks (fully flexible)",
            "apt_ev_penetration": "0%",
        },
        "stats": stats,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    if latest_dir.exists():
        for old in latest_dir.iterdir():
            if old.is_file():
                old.unlink()
    else:
        latest_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.iterdir():
        if path.is_file():
            target = latest_dir / path.name
            target.write_bytes(path.read_bytes())

    print(f"\nWrote {out_dir} ({stats['totals']['rows']:,} rows)")
    print("Archetype stats:")
    for name, s in stats["by_archetype"].items():
        mean_daily = s["kwh"] / max(s["home_days"], 1)
        flex_share = s["flexible_kwh"] / max(s["kwh"], 1e-9)
        print(
            f"  {name}: homes={s['homes']} ev={s['ev_homes']} "
            f"mean_daily_kwh={mean_daily:.2f} flex_share={flex_share:.3f}"
        )

    if args.upload_s3:
        upload_s3_dir(out_dir, args.bucket, args.s3_prefix)


if __name__ == "__main__":
    main()
