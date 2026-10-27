#!/usr/bin/env python3
"""Conservative rate limiting for Rocky Mountain Power scrapers.

Default: ~60s between hits so we do not trip RMP / Azure B2C rate limits.
Override via env (repo-root .env).
"""

from __future__ import annotations

import os
import random
import time


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def min_delay() -> float:
    return _env_float("RMP_MIN_DELAY_SEC", 60.0)


def max_delay() -> float:
    return max(min_delay(), _env_float("RMP_MAX_DELAY_SEC", 75.0))


def download_extra() -> float:
    return _env_float("RMP_DOWNLOAD_EXTRA_SEC", 15.0)


def error_backoff_base() -> float:
    return _env_float("RMP_ERROR_BACKOFF_SEC", 120.0)


def polite_sleep(kind: str = "action") -> float:
    """Jittered sleep between normal page/API actions. Returns seconds slept."""
    lo, hi = min_delay(), max_delay()
    if kind == "download":
        lo += download_extra()
        hi += download_extra()
    elif kind == "meter_switch":
        lo += 5.0
        hi += 15.0
    seconds = random.uniform(lo, hi)
    print(f"  [rate-limit] sleep {seconds:.1f}s ({kind})")
    time.sleep(seconds)
    return seconds


def backoff_sleep(attempt: int, reason: str = "error") -> float:
    """Exponential backoff with jitter after errors / 429 / timeouts."""
    base = error_backoff_base()
    exp = min(base * (2**attempt), 600.0)
    seconds = random.uniform(exp * 0.8, exp * 1.4)
    print(f"  [rate-limit] BACKOFF {seconds:.1f}s after {reason} (attempt {attempt})")
    time.sleep(seconds)
    return seconds
