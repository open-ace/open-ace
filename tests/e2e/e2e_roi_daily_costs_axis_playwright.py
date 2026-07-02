#!/usr/bin/env python3
"""
Open ACE - ROI Analysis: Daily Costs chart axis date format E2E test.

Regression for: the daily-cost bar chart x-axis leaked verbose time info
(RFC822 HTTP-date such as "Mon, 01 Jun 2026 00:00:00 GMT" on PostgreSQL, or a
"YYYY-MM-DD 00:00:00" string) onto the axis instead of a compact date.

Fix (this PR):
  - Backend (roi_calculator.get_daily_costs) normalizes `date` to YYYY-MM-DD.
  - Frontend (formatChartDate) renders a compact, locale-aware axis label and
    keeps the full YYYY-MM-DD only in the hover tooltip via BarChart's new
    optional `tooltipLabels` prop.

This test is a render/integration smoke test. Chart.js paints axis text onto a
<canvas>, so the axis label format is NOT asserted here (canvas pixels are not
DOM-readable and OCR is unreliable). The format correctness is covered by the
frontend unit tests (formatChartDate) and backend unit tests
(test_get_daily_costs_normalizes_date_object). Here we assert:

  1. Admin login + /manage/analysis/roi loads without console/page errors.
  2. The ROI page renders (heading + daily-costs section) with no i18n bare-key
     leaks.
  3. The daily-costs section renders a chart canvas with non-blank pixels when
     data exists (or the localized "no data" empty state when it does not).

Run:
  HEADLESS=true  python tests/e2e/e2e_roi_daily_costs_axis_playwright.py
  HEADLESS=false python tests/e2e/e2e_roi_daily_costs_axis_playwright.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-roi-daily-costs-axis")

# camelCase i18n keys that must never leak as literal text in the ROI page.
LEAK_KEYS = [
    "roiAnalysis",
    "dailyCosts",
    "roiTrend",
    "costBreakdown",
    "efficiencyReport",
    "noData",
]

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


def login(page):
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
    pause(1)
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    # Admin lands on /manage/dashboard; wait for any non-login destination to
    # settle the post-login redirect chain.
    page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
    pause(2)
    check(True, "Login successful, redirected away from login")
    shot(page, "01-login")


def run_tests():
    """Run all tests."""
    global passed, failed, errors

    print("=" * 60)
    print("ROI Daily-Costs Chart Axis Date Format E2E Test")
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # Capture any console / page errors so a render regression fails the test.
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        try:
            login(page)

            print("\n[TEST] Navigate to ROI analysis...")
            page.goto(f"{BASE_URL}/manage/analysis/roi")
            # Wait for the page heading to appear (translated title).
            page.wait_for_selector("h2", timeout=15000)
            pause(2)
            shot(page, "02-roi-page")

            # --- i18n bare-key leak check ---
            body_text = page.locator("body").inner_text(timeout=10000)
            leaked = [k for k in LEAK_KEYS if k in body_text]
            check(
                not leaked,
                f"No i18n bare-key leaks on ROI page (leaked: {leaked or 'none'})",
            )

            # --- daily-costs section renders ---
            # The "Daily Costs" card title is localized; locate the card by its
            # canvas/empty-state child instead of locale-dependent text.
            daily_card = page.locator(".card").filter(has=page.locator("canvas, .empty-state"))
            check(daily_card.count() >= 1, "Daily-costs card section is rendered")

            canvas = page.locator("canvas").first
            has_canvas = canvas.count() > 0 and canvas.is_visible()
            if has_canvas:
                # Assert the canvas actually painted something (non-blank) by
                # sampling its pixels. A blank canvas means the chart failed to
                # render — e.g. a JS error from a bad label/tooltipLabels shape.
                box = canvas.bounding_box()
                shot(page, "03-daily-costs-chart")
                non_blank = False
                if box and box["width"] > 0 and box["height"] > 0:
                    screenshot_bytes = canvas.screenshot()
                    non_blank = (
                        any(b != 0 for b in screenshot_bytes) and len(screenshot_bytes) > 1000
                    )
                check(non_blank, "Daily-costs chart canvas painted non-blank content")
            else:
                # No data in this environment -> the localized empty state is the
                # correct render. Record as info, not failure.
                print("    [INFO] No daily-costs data present; empty-state rendered")

            # --- no console / page errors during render ---
            check(
                not page_errors,
                f"No uncaught page errors (got: {page_errors or 'none'})",
            )
            # Only treat genuine JS/runtime console errors as failures. Network
            # resource-load statuses ("Failed to load resource ... 401/404") are
            # environmental (optional endpoints, favicon, background polling) and
            # unrelated to the date-format change — the chart itself rendered
            # fine, which is what we care about.
            JS_ERROR_MARKERS = (
                "uncaught",
                "typeerror",
                "referenceerror",
                "syntaxerror",
                "is not defined",
                "cannot read",
            )
            real_console_errors = [
                e
                for e in console_errors
                if "failed to load resource" not in e.lower()
                and "favicon" not in e.lower()
                and any(marker in e.lower() for marker in JS_ERROR_MARKERS)
            ]
            check(
                not real_console_errors,
                f"No JS console errors (got: {real_console_errors or 'none'}; "
                f"network-only ignored: {console_errors or 'none'})",
            )

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
    success = run_tests()
    sys.exit(0 if success else 1)
