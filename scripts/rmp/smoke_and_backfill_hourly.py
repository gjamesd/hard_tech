#!/usr/bin/env python3
"""Hourly RMP pull via UI (getIntervalUsageForDate is request-encrypted).

1) Login, dismiss cookie banner
2) For each meter: One Day view, set ending date to yesterday
3) Capture interval JSON; walk PREVIOUS day-by-day to RMP_START_DATE
4) Resume via hourly_interval_progress.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from rate_limit import backoff_sleep, polite_sleep

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

RAW = ROOT / "data" / "raw" / "rmp"
HOURLY = RAW / "hourly"
PROGRESS = RAW / "hourly_interval_progress.json"

LOGIN_URL = "https://csapps.rockymountainpower.net/idm/login"
USAGE_URL = "https://csapps.rockymountainpower.net/secure/my-account/energy-usage"

# Folder keys are anonymized labels (no street address). Serials select meters in the UI.
METERS = [
    {
        "key": "50752799_casita",
        "serial": "50752799",
    },
    {
        "key": "342856918_sfh",
        "serial": "342856918",
    },
]


def require_creds() -> tuple[str, str]:
    user = os.getenv("RMP_USERNAME", "").strip()
    password = os.getenv("RMP_PASSWORD", "").strip()
    if not user or not password:
        sys.exit("Missing RMP_USERNAME / RMP_PASSWORD in .env")
    return user, password


def abort_if_auth_broken(page) -> None:
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    if "authorization_request_not_found" in text.lower():
        sys.exit(
            "Auth broken ([authorization_request_not_found]). "
            "Rotate password, update .env, wait a few minutes, retry headed."
        )


def login(page, username: str, password: str) -> None:
    context = page.context
    context.clear_cookies()
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    abort_if_auth_broken(page)

    page.wait_for_selector("#loginframe", timeout=30000)
    frame = page.frame(name="loginframe")
    if frame is None:
        for fr in page.frames:
            if "B2C" in (fr.url or "") or "signin" in (fr.url or "").lower():
                frame = fr
                break
    if frame is None:
        sys.exit("Login iframe not found")

    user_ok = False
    for sel in (
        'input[name="signInName"]',
        "#signInName",
        'input[type="email"]',
        'input[type="text"]',
    ):
        loc = frame.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(username)
            user_ok = True
            break
    if not user_ok:
        sys.exit("User ID field not found in login iframe")

    pass_ok = False
    for sel in ('input[name="password"]', "#password", 'input[type="password"]'):
        loc = frame.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(password)
            pass_ok = True
            break
    if not pass_ok:
        sys.exit("Password field not found in login iframe")

    clicked = False
    for sel in (
        'button[type="submit"]',
        "#next",
        'button:has-text("Sign in")',
        'button:has-text("SIGN IN")',
    ):
        loc = frame.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.click()
            clicked = True
            break
    if not clicked:
        sys.exit("Sign in button not found")

    try:
        page.wait_for_url(re.compile(r"/secure/"), timeout=90000)
    except PlaywrightTimeout:
        print("Waiting up to 3 min for MFA / manual finish in the browser…")
        page.wait_for_url(re.compile(r"/secure/"), timeout=180000)

    abort_if_auth_broken(page)
    print(f"Logged in → {page.url}")
    polite_sleep("action")


def open_usage(page) -> None:
    page.goto(USAGE_URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    abort_if_auth_broken(page)
    print(f"Usage → {page.url}")
    polite_sleep("action")


def start_date() -> date:
    raw = os.getenv("RMP_START_DATE", "2026-01-01").strip()
    return date.fromisoformat(raw)


def load_progress() -> dict:
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text())
    return {"completed": [], "failures": 0}


def save_progress(p: dict) -> None:
    PROGRESS.write_text(json.dumps(p, indent=2) + "\n")


def intervals(payload: dict):
    body = payload.get("getIntervalUsageForDateResponseBody") or {}
    return (body.get("response") or {}).get("intervalDataResponse")


def ok(payload: dict) -> bool:
    rows = intervals(payload)
    return isinstance(rows, list) and len(rows) > 0


def pick_date_from_payload(payload: dict) -> str | None:
    rows = intervals(payload) or []
    if rows:
        return rows[0].get("readDate")
    return None


def dismiss_banner(page) -> None:
    try:
        btn = page.get_by_role("button", name=re.compile(r"^CLOSE$", re.I))
        if btn.count() and btn.first.is_visible():
            btn.first.click(timeout=3000)
            page.wait_for_timeout(500)
            print("dismissed cookie banner")
    except Exception:
        pass


def select_period_one_day(page) -> None:
    page.locator("mat-select").nth(1).click(timeout=20000)
    page.wait_for_timeout(500)
    for i in range(page.locator("mat-option").count()):
        t = page.locator("mat-option").nth(i).inner_text().strip()
        if re.match(r"One\s*Day", t, re.I):
            page.locator("mat-option").nth(i).click()
            print("period → One Day")
            page.wait_for_timeout(2500)
            return
    raise RuntimeError("One Day not found")


def select_meter(page, serial: str) -> None:
    serial = serial.strip()
    wait_loading_clear(page)
    page.locator("mat-select").first.click(force=True, timeout=20000)
    page.wait_for_timeout(500)
    for i in range(page.locator("mat-option").count()):
        t = page.locator("mat-option").nth(i).inner_text()
        compact = re.sub(r"\s+", "", t)
        if serial in t or serial in compact:
            page.locator("mat-option").nth(i).click(force=True)
            print(f"meter → serial {serial}")
            wait_loading_clear(page)
            return
    raise RuntimeError(f"meter serial {serial} not found")


def sync_done_from_disk(done: set[str]) -> set[str]:
    for meter in METERS:
        folder = HOURLY / meter["key"]
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            if path.name.startswith("_"):
                continue
            day = path.stem
            try:
                payload = json.loads(path.read_text())
            except Exception:
                continue
            if ok(payload):
                done.add(f"{meter['key']}|{day}")
    return done


def wait_loading_clear(page, timeout_ms: int = 45000) -> None:
    try:
        page.wait_for_function(
            """() => {
              const el = document.querySelector('#loading-component');
              if (!el) return true;
              const style = window.getComputedStyle(el);
              return style.display === 'none' || style.visibility === 'hidden'
                || el.offsetParent === null || el.clientHeight === 0;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        # Stuck spinner — force-remove so clicks can proceed
        page.evaluate(
            """() => {
              for (const el of document.querySelectorAll(
                '#loading-component, .overlay.ng-star-inserted, p.loading-text'
              )) {
                el.remove();
              }
            }"""
        )
        print("  forced-clear stuck Loading overlay")
        page.wait_for_timeout(500)


def date_input(page):
    # RMP: <input placeholder="Show usage through :" min=... max=...>
    loc = page.locator('input[placeholder*="Show usage through"]')
    if loc.count():
        return loc.first
    loc = page.locator("input[matinput][aria-haspopup='true']")
    if loc.count():
        return loc.first
    raise RuntimeError("ending-on date input not found")


def parse_ui_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    parts = raw.split("/")
    if len(parts) == 3:
        try:
            month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return date(year, month, day)
        except ValueError:
            return None
    return None


def read_ui_date(page) -> date | None:
    try:
        return parse_ui_date(date_input(page).input_value())
    except Exception:
        return None


def format_ui_date(day: date) -> str:
    # Match portal display (no leading zeros): 9/19/2026
    return f"{day.month}/{day.day}/{day.year}"


def ui_matches(day: date, raw: str) -> bool:
    parsed = parse_ui_date(raw)
    return parsed == day


def set_ending_date(page, day: date) -> None:
    """Type the date like a human. Do NOT press Escape — it reverts the field."""
    wait_loading_clear(page)
    date_str = format_ui_date(day)
    current = ""
    for attempt in range(3):
        inp = date_input(page)
        inp.scroll_into_view_if_needed()
        inp.click(force=True, timeout=20000)
        page.wait_for_timeout(250)
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(date_str, delay=35)
        page.keyboard.press("Tab")
        page.wait_for_timeout(800)
        try:
            current = date_input(page).input_value().strip()
        except Exception:
            current = ""
        if ui_matches(day, current):
            break
        print(f"  date did not stick (attempt {attempt + 1}: typed {date_str!r}, ui={current!r})")
        wait_loading_clear(page)
    wait_loading_clear(page)
    print(f"day → {day.isoformat()} (typed {date_str!r}, ui={current!r})")
    if not ui_matches(day, current):
        raise RuntimeError(f"date input stuck at {current!r}, wanted {date_str!r}")


def _click_nav(page, label: str) -> None:
    wait_loading_clear(page)
    # Nuke overlays that silently eat clicks
    page.evaluate(
        """() => {
          for (const el of document.querySelectorAll(
            '#loading-component, .overlay.ng-star-inserted, p.loading-text, .cdk-overlay-backdrop'
          )) { el.remove(); }
        }"""
    )
    clicked = page.evaluate(
        """(label) => {
          const re = new RegExp('^\\\\s*' + label + '\\\\s*$', 'i');
          const el = [...document.querySelectorAll('button, a, span, div')]
            .find(b => re.test((b.textContent || '').trim()) && b.offsetParent !== null);
          if (!el) return false;
          el.scrollIntoView({block:'center'});
          el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
          return true;
        }""",
        label,
    )
    if not clicked:
        page.get_by_role("button", name=re.compile(rf"^{label}$", re.I)).click(
            force=True, timeout=15000
        )


def click_previous(page) -> None:
    _click_nav(page, "PREVIOUS")


def click_next(page) -> None:
    _click_nav(page, "NEXT")


def save_file(meter_key: str, day_str: str, payload: dict) -> Path:
    dest = HOURLY / meter_key / f"{day_str}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    return dest


def wait_for_interval(page, trigger, timeout_ms: int = 90000) -> dict | None:
    """Capture getIntervalUsageForDate via expect_response (listener races were dropping bodies)."""
    wait_loading_clear(page)
    try:
        with page.expect_response(
            lambda r: "getIntervalUsageForDate" in r.url,
            timeout=timeout_ms,
        ) as pending:
            trigger()
        resp = pending.value
        if resp.status != 200:
            print(f"  interval HTTP {resp.status}")
            return None
        body = resp.json()
        if ok(body):
            return body
        print("  interval body empty/not-ok")
        return None
    except Exception as exc:
        print(f"  expect_response: {exc}")
        return None
    finally:
        wait_loading_clear(page)


def payload_day(payload: dict) -> date | None:
    raw = pick_date_from_payload(payload)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def good_day(payload: dict | None, target: date) -> bool:
    return bool(payload and ok(payload) and payload_day(payload) == target)


def ensure_ui_day(page, day: date) -> bool:
    if read_ui_date(page) == day:
        return True
    try:
        wait_for_interval(page, lambda d=day: set_ending_date(page, d))
    except Exception as exc:
        print(f"  ensure_ui_day type error: {exc}")
    if read_ui_date(page) == day:
        return True
    try:
        set_ending_date(page, day)
    except Exception as exc:
        print(f"  ensure_ui_day retry error: {exc}")
    return read_ui_date(page) == day


def fetch_day(page, target: date) -> dict | None:
    """Prefer PREVIOUS from target+1; if nav is dead, type the target date."""
    anchor = target + timedelta(days=1)

    for attempt in range(1, 5):
        ui = read_ui_date(page)
        if ui == target and attempt == 1:
            print(f"  on target already; NEXT → anchor then PREVIOUS (attempt {attempt})")
            wait_for_interval(page, lambda: click_next(page), timeout_ms=45000)
            ui = read_ui_date(page)

        if ui != anchor:
            print(f"  move to anchor {anchor.isoformat()} (attempt {attempt})")
            if not ensure_ui_day(page, anchor):
                print(f"  could not reach anchor {anchor} (ui={read_ui_date(page)})")
                # last resort: type target directly
                print(f"  typing target {target.isoformat()} directly")
                payload = wait_for_interval(
                    page, lambda d=target: set_ending_date(page, d), timeout_ms=90000
                )
                if good_day(payload, target):
                    return payload
                continue

        print(f"  PREVIOUS → {target.isoformat()} (attempt {attempt})")
        payload = wait_for_interval(page, lambda: click_previous(page), timeout_ms=45000)
        if good_day(payload, target):
            return payload

        ui_after = read_ui_date(page)
        if payload and ok(payload):
            print(f"  wrong day in response: wanted {target} got {payload_day(payload)}")
        else:
            print(f"  no payload after PREVIOUS (ui={ui_after})")

        if ui_after == target:
            print("  UI advanced without capture — NEXT back to anchor")
            wait_for_interval(page, lambda: click_next(page), timeout_ms=45000)
            continue

        # PREVIOUS did not move the date — type the target
        print(f"  PREVIOUS stuck on {ui_after}; typing {target.isoformat()}")
        payload = wait_for_interval(
            page, lambda d=target: set_ending_date(page, d), timeout_ms=90000
        )
        if good_day(payload, target):
            return payload
        if read_ui_date(page) == target and not payload:
            print("  typed date stuck without API — forcing page reload")
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
            except Exception as exc:
                print(f"  reload failed: {exc}")

    return None


def latest_scraped_day(meter_key: str | None = None) -> date | None:
    found: list[date] = []
    meters = [m for m in METERS if meter_key is None or m["key"] == meter_key]
    for meter in meters:
        folder = HOURLY / meter["key"]
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                found.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
    return max(found) if found else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RMP hourly UI scrape")
    p.add_argument("--only-meter", choices=[m["key"] for m in METERS], default=None)
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD cap (inclusive)")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD floor (inclusive)")
    p.add_argument("--max-days", type=int, default=None, help="Stop after N new saves")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    HOURLY.mkdir(parents=True, exist_ok=True)
    user, pw = require_creds()
    progress = load_progress()
    done = sync_done_from_disk(set(progress.get("completed", [])))
    progress["completed"] = sorted(done)
    save_progress(progress)

    meters = [m for m in METERS if args.only_meter is None or m["key"] == args.only_meter]

    # Never ask for "yesterday" if AMI hasn't published it yet.
    scraped_max = latest_scraped_day(args.only_meter)
    end = scraped_max or (date.today() - timedelta(days=2))
    if args.end_date:
        end = min(end, date.fromisoformat(args.end_date))
    start = date.fromisoformat(args.start_date) if args.start_date else start_date()
    if end < start:
        sys.exit(f"end {end} before start {start}")
    days = []
    cur = end
    while cur >= start:
        days.append(cur)
        cur -= timedelta(days=1)
    print(f"Using end={end.isoformat()} (from scraped max / safe lag), start={start.isoformat()}")

    remaining = sum(
        1
        for m in meters
        for d in days
        if f"{m['key']}|{d.isoformat()}" not in done
    )
    print(
        f"Range {start} → {end}: {len(days)} days × {len(meters)} meters; "
        f"{remaining} remaining (already done={len(done)})"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        login(page, user, pw)
        open_usage(page)
        abort_if_auth_broken(page)
        dismiss_banner(page)

        failures = 0
        saved_this_run = 0
        for meter in meters:
            meter_days = [d for d in days if f"{meter['key']}|{d.isoformat()}" not in done]
            if not meter_days:
                print(f"\n=== {meter['key']}: all days done ===")
                continue

            print(f"\n=== {meter['key']}: {len(meter_days)} days ===")
            try:
                select_meter(page, meter["serial"])
            except Exception as exc:
                print(f"meter select failed: {exc}")
                open_usage(page)
                dismiss_banner(page)
                select_meter(page, meter["serial"])
            polite_sleep("meter_switch")

            period_text = page.locator("mat-select").nth(1).inner_text()
            if not re.search(r"One\s*Day", period_text, re.I):
                wait_for_interval(page, lambda: select_period_one_day(page))
                polite_sleep("action")

            # Anchor on the day after the first missing target so PREVIOUS works.
            first = meter_days[0]
            anchor = first + timedelta(days=1)
            ui_day = read_ui_date(page)
            if ui_day != anchor and ui_day != first:
                print(f"anchoring UI at {anchor.isoformat()} before {first.isoformat()}")
                wait_for_interval(page, lambda d=anchor: set_ending_date(page, d))
                polite_sleep("action")

            for target in meter_days:
                key = f"{meter['key']}|{target.isoformat()}"
                if key in done:
                    continue
                day_tries = 0
                while key not in done and day_tries < 6:
                    day_tries += 1
                    print(f"\n→ {meter['key']} {target.isoformat()} (try {day_tries})")
                    try:
                        payload = fetch_day(page, target)
                    except Exception as exc:
                        print(f"  exception: {exc}")
                        payload = None
                    polite_sleep("action")

                    if good_day(payload, target):
                        path = save_file(meter["key"], target.isoformat(), payload)
                        done.add(key)
                        progress["completed"] = sorted(done)
                        progress["failures"] = 0
                        save_progress(progress)
                        print(f"SAVED {path} ({len(intervals(payload))} intervals)")
                        failures = 0
                        saved_this_run += 1
                        break

                    failures += 1
                    progress["failures"] = failures
                    save_progress(progress)
                    print(f"FAIL {meter['key']} {target.isoformat()} (try {day_tries})")
                    backoff_sleep(min(failures, 2), "day_fail")
                    if failures >= 20:
                        print("Too many failures — stopping (progress kept)")
                        browser.close()
                        print(f"Done so far: {len(done)} completed keys")
                        return
                    try:
                        open_usage(page)
                        dismiss_banner(page)
                        select_meter(page, meter["serial"])
                        period_text = page.locator("mat-select").nth(1).inner_text()
                        if not re.search(r"One\s*Day", period_text, re.I):
                            wait_for_interval(page, lambda: select_period_one_day(page))
                        ensure_ui_day(page, target + timedelta(days=1))
                    except Exception as exc:
                        print(f"  recover failed: {exc}")

                if key not in done:
                    print(f"SKIP {meter['key']} {target.isoformat()} after {day_tries} tries")
                if args.max_days and saved_this_run >= args.max_days:
                    print(f"Hit --max-days={args.max_days}; stopping")
                    browser.close()
                    print(f"Done so far: {len(done)} completed keys")
                    return

        browser.close()
        print(f"\nDone: {len(done)} completed keys → {HOURLY}")


if __name__ == "__main__":
    main()
