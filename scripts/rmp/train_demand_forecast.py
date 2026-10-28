#!/usr/bin/env python3
"""Train pickled Wasatch + PACE next-day demand models (sklearn HGB + Open-Meteo)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "streamlit_demo"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from lib.forecast import train_and_save  # noqa: E402


def main() -> None:
    print("Training Wasatch + PACE HistGradientBoosting models…")
    meta = train_and_save()
    for name, m in (("wasatch", meta["wasatch"]), ("pace", meta["pace"])):
        print(
            f"  {name}: holdout={m['holdout_date']} "
            f"MAE={m['mae_mw']:.1f} MW  MAPE={m.get('mape', float('nan')):.3f} "
            f"n_train={m['n_train_rows']}"
        )
    print("Wrote", meta["paths"])


if __name__ == "__main__":
    main()
