#!/usr/bin/env python3
"""
Open ACE - Audit Log Default Date Range E2E Playwright Test (issue #838)

Verifies the audit-center page (/manage/audit) pre-populates its start/end
date inputs with a sensible default (last 7 days) on entry, instead of
leaving them empty and querying an unbounded range.

Tests:
  1. Login as admin
  2. Navigate to /manage/audit
  3. Two date inputs are present and NOT empty (regression: was empty pre-fix)
  4. start_date == 7 days ago, end_date == today (YYYY-MM-DD)
  5. The audit-logs API request carries the default start_date/end_date params
  6. Reset restores the same default range

Run:
  HEADLESS=true  python tests/e2e/e2e_audit_log_default_dates_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_audit_log_default_dates_playwright.py   # 演示模式
"""

import os
import sys
import time
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-audit-default-dates")

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    time.sleep(seconds if not HEADLESS else 0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def expected_dates():
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    return week_ago, today


def login(page):
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    pause(2)
    # Wait for redirect off the login page (auth race guard).
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    check(True, "Login successful, redirected away from /login")
    shot(page, "01-login")


def run_tests():
    global passed, failed, errors
    print("=" * 60)
    print("Audit Log Default Date Range E2E Tests (#838)")
    print(f"BASE_URL: {BASE_URL}  HEADLESS: {HEADLESS}")
    print("=" * 60)

    week_ago, today = expected_dates()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Capture audit-logs API requests to prove the default dates reach the backend.
        audit_requests = []

        def on_request(req):
            if "/api/governance/audit-logs" in req.url or "/audit/logs" in req.url:
                audit_requests.append(req.url)

        page.on("request", on_request)

        try:
            login(page)

            print("\n[TEST] Navigate to /manage/audit...")
            page.goto(f"{BASE_URL}/manage/audit")
            pause(2)

            date_inputs = page.locator("input[type='date']")
            date_inputs.first.wait_for(state="visible", timeout=10000)
            check(date_inputs.count() == 2, "Two date inputs are present")
            shot(page, "02-audit-page")

            start_val = date_inputs.nth(0).input_value()
            end_val = date_inputs.nth(1).input_value()
            print(f"    [INFO] start_date={start_val!r} end_date={end_val!r}")

            check(bool(start_val), "Start date input is NOT empty (regression)")
            check(bool(end_val), "End date input is NOT empty (regression)")
            check(start_val == week_ago, f"Start date == 7 days ago ({week_ago})")
            check(end_val == today, f"End date == today ({today})")

            # The default dates must reach the backend query.
            pause(1)
            joined = " ".join(audit_requests)
            check(
                f"start_date={week_ago}" in joined and f"end_date={today}" in joined,
                "Audit-logs API request carries the default start_date/end_date params",
            )
            shot(page, "03-default-range")

            # Reset must restore the same default range (not clear to empty).
            print("\n[TEST] Reset restores default range...")
            reset_btn = page.get_by_role("button", name="Reset").first
            if reset_btn.is_visible():
                reset_btn.click()
                pause(1)
                rv_start = date_inputs.nth(0).input_value()
                rv_end = date_inputs.nth(1).input_value()
                check(
                    rv_start == week_ago and rv_end == today,
                    "Reset restores the 7-day default range",
                )
            else:
                print("    [INFO] Reset button not found; skipping reset test")
            shot(page, "04-after-reset")

        except Exception as e:
            print(f"\n[ERROR] Test execution failed: {e}")
            try:
                shot(page, "error-state")
            except Exception:
                pass
            failed += 1
            errors.append(f"Test execution failed: {e}")
        finally:
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed} passed, {failed} failed")
    if errors:
        print("Errors:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
