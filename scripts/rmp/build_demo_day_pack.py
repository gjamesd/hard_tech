#!/usr/bin/env python3
"""Build demo day packs: demand + EIA PACE + weather + congestion + flex shift."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "streamlit_demo"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from lib.aggregate import available_dates  # noqa: E402
from lib.day_pipeline import build_day_pack  # noqa: E402
from lib.paths import HERO_DATES, WASATCH_DAY_DIR  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Wasatch demo day packs")
    p.add_argument("--date", action="append", dest="dates")
    p.add_argument("--heroes", action="store_true")
    p.add_argument("--from-wasatch", action="store_true", help="All dates in wasatch_residential_day")
    p.add_argument("--no-fetch", action="store_true", help="Skip EIA/Open-Meteo (demand only)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.from_wasatch:
        dates = sorted(
            p.name
            for p in WASATCH_DAY_DIR.iterdir()
            if p.is_dir() and len(p.name) == 10
        )
    elif args.dates:
        dates = args.dates
    else:
        dates = list(HERO_DATES)

    have = set(available_dates())
    resolved = []
    for d in dates:
        if d in have:
            resolved.append(d)
        else:
            print(f"skip missing housing date {d}")
    if not resolved:
        avail = sorted(have)
        july = [d for d in avail if d.startswith("2026-07")]
        resolved = [
            july[len(july) // 2] if july else avail[len(avail) // 2],
            avail[-1],
        ]
        resolved = sorted(set(resolved))
        print(f"Heroes missing — using {resolved}")

    for d in resolved:
        print(f"→ building pack {d}")
        pack = build_day_pack(d, fetch_grid=not args.no_fetch)
        meta = (pack / "meta.json").read_text()
        print(f"  {pack} grid_ok excerpt: {meta[:200]}…")


if __name__ == "__main__":
    main()
