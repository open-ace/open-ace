#!/usr/bin/env python3
"""
Conversation History - Default Date Range E2E test (Issue #1006)

Verifies the fix for: on the Conversation History page the start/end date
pickers were empty on load (and again after Reset), even though the backend
silently applied its own 90-day window — a UI/query range mismatch. The fix
pre-fills a 30-day default into the filter state on load AND on Reset.

Tests:
1. First load — the start/end date pickers are pre-filled with the default
   30-day range (today-30 .. today), NOT empty.
2. Reset regression — after manually changing the dates and clicking Reset,
   the pickers return to the default 30-day range (NOT empty).
3. After Reset the page still renders within the default range (rows or a
   clean empty state — not a crash).

#2457 realignment: the date filters are shared DatePicker components that
render as BUTTONS (value text + calendar icon), not input[type=date] — the
baselined Locator.input_value timeout. Values are read from the button text
and changed through the react-datepicker popup. The curl login + session
cookie is kept (it dodges the urllib->gevent 502 that affects requests).

Run:
  BASE_URL=http://localhost:19888 HEADLESS=true  python tests/issues/1006/e2e_conversation_history_default_dates.py
  BASE_URL=http://localhost:19888 HEADLESS=false python tests/issues/1006/e2e_conversation_history_default_dates.py
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots/e2e-conv-history-default-dates"

MANAGE_PATH = "/manage/analysis/conversation-history"

passed = 0
failed = 0


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=True)
    print(f"  screenshot: {name}.png")


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {desc} {detail}")
    else:
        failed += 1
        print(f"  FAIL: {desc} {detail}")


def expected_range(days=30):
    """Mirror of the frontend getDefaultDateRange(): local today-30 .. today."""
    today = datetime.now()
    start = today - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def norm_date_text(text):
    """The DatePicker button renders the value with slashes (2026/07/24) while
    the underlying filter state uses dashes (2026-07-24)."""
    return text.strip().replace("/", "-")


def clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (USERNAME,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def login_via_curl(base_url, username, password):
    """Authenticate via curl, dodging the urllib->gevent 502 issue that affects
    `requests`. Returns (session_token, status_code)."""
    jar = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    jar.close()
    try:
        proc = subprocess.run(
            [
                "curl",
                "-s",
                "-c",
                jar.name,
                "-X",
                "POST",
                f"{base_url}/api/auth/login",
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps({"username": username, "password": password}),
                "-o",
                os.devnull,
                "-w",
                "%{http_code}",
                "--max-time",
                "10",
            ],
            capture_output=True,
            text=True,
        )
        status = proc.stdout.strip()
        token = None
        if os.path.exists(jar.name):
            with open(jar.name) as f:
                for line in f:
                    # The cookie value line is the only one containing the
                    # cookie name; note HttpOnly cookies are stored with a
                    # "#HttpOnly_<domain>" prefix, so a blanket startswith("#")
                    # filter would wrongly skip them.
                    if "session_token" in line:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) >= 7:
                            token = parts[6]
                            break
        return token, status
    finally:
        try:
            os.unlink(jar.name)
        except OSError:
            pass


def pick_non_default_day(page):
    """Open the start-date popup and click a day that differs from the default
    range's day-of-month, so the change is guaranteed observable."""
    page.locator(".open-ace-datepicker button").first.click()
    page.wait_for_selector(".react-datepicker", timeout=5000)
    cells = page.locator(".react-datepicker__day:not(.react-datepicker__day--outside-month)")
    today_d = datetime.now().day
    start_d = (datetime.now() - timedelta(days=30)).day
    avoid = {today_d, start_d}
    count = cells.count()
    for i in range(count):
        text = cells.nth(i).inner_text().strip()
        if text.isdigit() and int(text) not in avoid:
            cells.nth(i).click()
            return True
    page.keyboard.press("Escape")
    return False


def test_default_dates():
    global passed, failed
    print("=" * 60)
    print("Conversation History - Default Date Range E2E")
    print(f"BASE_URL={BASE_URL}  HEADLESS={HEADLESS}")
    print("=" * 60)

    clear_seeded_password_gate()

    # Authenticate via curl (urllib->gevent returns 502 with `requests`; curl
    # works) and reuse the session cookie in the browser context.
    token, status = login_via_curl(BASE_URL, USERNAME, PASSWORD)
    check("Login", status == "200", f"(status {status})")
    check("Got session_token", bool(token))
    if not token:
        print("\n[ABORT] No session token; cannot proceed.")
        return False

    exp_start, exp_end = expected_range(30)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )
        page = context.new_page()

        print("\n[1] First load — date pickers pre-filled (not empty)")
        page.goto(f"{BASE_URL}{MANAGE_PATH}", wait_until="networkidle")
        page.wait_for_timeout(4000)
        try:
            page.wait_for_selector(".open-ace-datepicker button", timeout=15000)
        except Exception:
            check("Date pickers present", False, "- timeout")
        pickers = page.locator(".open-ace-datepicker button")
        check("Two date pickers present", pickers.count() >= 2, f"(found {pickers.count()})")

        start_val = norm_date_text(pickers.nth(0).inner_text())
        end_val = norm_date_text(pickers.nth(1).inner_text())
        check(
            "Start date NOT empty on load",
            start_val not in ("", "Select.date"),
            f"(value={start_val!r})",
        )
        check(
            "End date NOT empty on load", end_val not in ("", "Select.date"), f"(value={end_val!r})"
        )
        check(
            "Start date == today-30 on load",
            start_val == exp_start,
            f"(got {start_val!r}, want {exp_start!r})",
        )
        check(
            "End date == today on load",
            end_val == exp_end,
            f"(got {end_val!r}, want {exp_end!r})",
        )
        shot(page, "01_default_loaded")

        print("\n[2] Reset restores the default range (regression point)")
        # Manually move the start date off the default via the popup.
        changed = pick_non_default_day(page)
        page.wait_for_timeout(800)
        start_changed = norm_date_text(
            page.locator(".open-ace-datepicker button").nth(0).inner_text()
        )
        check(
            "Manual change applied",
            changed and start_changed != exp_start,
            f"(got {start_changed!r})",
        )

        reset_btn = page.locator("button:has-text('Reset'), button:has-text('重置')")
        check("Reset button found", reset_btn.count() > 0, f"(found {reset_btn.count()})")
        if reset_btn.count() > 0:
            reset_btn.first.click()
            page.wait_for_timeout(2500)

        pickers = page.locator(".open-ace-datepicker button")
        start_after = norm_date_text(pickers.nth(0).inner_text())
        end_after = norm_date_text(pickers.nth(1).inner_text())
        check(
            "Start date NOT empty after reset",
            start_after not in ("", "Select.date"),
            f"(value={start_after!r})",
        )
        check(
            "End date NOT empty after reset",
            end_after not in ("", "Select.date"),
            f"(value={end_after!r})",
        )
        check(
            "Start date == default after reset",
            start_after == exp_start,
            f"(got {start_after!r}, want {exp_start!r})",
        )
        check(
            "End date == default after reset",
            end_after == exp_end,
            f"(got {end_after!r}, want {exp_end!r})",
        )
        shot(page, "02_after_reset")

        print("\n[3] Page still renders within default range after reset")
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text().lower()
        table_present = page.locator("table").count() > 0
        # Rows shown, OR a clean no-data/empty state — either is correct; we
        # only assert the page did not crash and the result area rendered.
        rendered = table_present or "no data" in body or "conversations" in body
        check("Result area rendered after reset", rendered)

        browser.close()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    assert failed == 0, f"{failed} default-date checks failed"
    return failed == 0


if __name__ == "__main__":
    import sys

    ok = test_default_dates()
    sys.exit(0 if ok else 1)
