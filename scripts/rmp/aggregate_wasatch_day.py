#!/usr/bin/env python3
"""Layer 1 — aggregate 5k synthetic homes ×SCALE → Wasatch residential day curves."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "streamlit_demo"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from lib.aggregate import (  # noqa: E402
    aggregate_fleet_hourly,
    available_dates,
    scale_to_wasatch,
    write_wasatch_days,
)
from lib.paths import HERO_DATES, WASATCH_DAY_DIR  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate + scale Wasatch residential day")
    p.add_argument("--date", action="append", dest="dates", help="YYYY-MM-DD (repeatable)")
    p.add_argument("--all-dates", action="store_true", help="Aggregate every available date")
    p.add_argument("--heroes", action="store_true", help="Only hero dates")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.all_dates:
        dates = None
        print("Aggregating all dates in housing_universe/latest …")
    elif args.heroes:
        dates = list(HERO_DATES)
        print(f"Aggregating hero dates: {dates}")
    elif args.dates:
        dates = args.dates
        print(f"Aggregating dates: {dates}")
    else:
        dates = list(HERO_DATES)
        print(f"Default: hero dates {dates} (pass --all-dates for full range)")

    if dates:
        have = set(available_dates())
        missing = [d for d in dates if d not in have]
        if missing:
            # Fall back to nearest available summer-ish / latest
            avail = sorted(have)
            print(f"Requested missing from universe: {missing}")
            if not avail:
                sys.exit("No dates in housing universe")
            dates = []
            for d in list(HERO_DATES) + (args.dates or []):
                if d in have:
                    dates.append(d)
            if not dates:
                # pick mid-July-ish and latest
                july = [d for d in avail if d.startswith("2026-07")]
                dates = [july[len(july) // 2] if july else avail[len(avail) // 2], avail[-1]]
                dates = sorted(set(dates))
            print(f"Using available dates: {dates}")

    fleet = aggregate_fleet_hourly(dates=dates)
    scaled = scale_to_wasatch(fleet)
    out = write_wasatch_days(scaled, WASATCH_DAY_DIR)
    peak = scaled.loc[scaled.groupby("date")["wasatch_mw"].idxmax()][
        ["date", "hour_ending", "wasatch_mw"]
    ]
    print(f"Wrote {out} ({scaled['date'].nunique()} days, {len(scaled)} hourly rows)")
    print("Peak Wasatch MW by day:")
    for _, row in peak.iterrows():
        print(f"  {row['date']} HE{int(row['hour_ending']):02d}: {row['wasatch_mw']:.1f} MW")


if __name__ == "__main__":
    main()
