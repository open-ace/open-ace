#!/usr/bin/env python3
"""
Open ACE - Anomaly Detection "All" Button E2E Playwright Test

Mirrors tests/e2e/e2e_token_trend_all_button_playwright.py (Issue #802) for the
anomaly detection page. Verifies the "All" quick-range reflects the system's
actual data range (from /api/analysis/data-range) instead of a hardcoded window.

Tests:
1. Login as admin
2. Navigate to Anomaly Detection page
3. Verify default date range is 30 days
4. Click "All" button
5. Verify date range shows the actual data range from the API
6. Verify the /api/analysis/data-range endpoint is hit and returns min/max_date
7. Manual date input: transition into "All" overwrites with data range;
   editing within "All" preserves the manual value (two-phase semantics)

Run:
  HEADLESS=true  python tests/e2e/e2e_anomaly_all_button_playwright.py   # automated
  HEADLESS=false python tests/e2e/e2e_anomaly_all_button_playwright.py   # demo
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-anomaly-all")

passed = 0
failed = 0
errors = []
captured_data_range = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


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
    page.goto(f"{BASE_URL}/login")
    pause(1)
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    pause(2)
    page.wait_for_url("**/work**", timeout=10000)
    check(True, "Login successful, redirected to work page")
    shot(page, "01-login")


def navigate_to_anomaly(page):
    print("\n[TEST] Navigate to Anomaly Detection...")
    page.goto(f"{BASE_URL}/manage/analysis/anomaly")
    pause(2)
    shot(page, "02-anomaly")


def find_all_button(page):
    btn = page.locator(".btn-group button:text('All')").first
    if btn.count() == 0:
        btn = page.locator(".btn-group button:text('全部')").first
    return btn


def find_button_by_text(page, text):
    btn = page.locator(f".btn-group button:text('{text}')").first
    if btn.count() == 0:
        btn = page.locator(f".btn-group button:text('{text} 天')").first
    return btn


def test_default_date_range(page):
    print("\n[TEST] Default date range (30 days)...")
    active_button = page.locator(".btn-group .btn-primary")
    check(active_button.first.is_visible(), "Primary button is visible")
    button_text = active_button.first.text_content()
    check("30" in (button_text or ""), f"Active button shows '30' (text: '{button_text}')")

    start_input = page.locator("input[type='date']").first
    end_input = page.locator("input[type='date']").nth(1)
    end_value = end_input.input_value()
    today = datetime.now().strftime("%Y-%m-%d")
    check(end_value == today, f"End date shows today ({end_value} vs {today})")
    start_value = start_input.input_value()
    expected_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    check(
        start_value == expected_start,
        f"Start date shows 30 days ago ({start_value} vs {expected_start})",
    )
    shot(page, "03-default-30-days")


def test_all_button_click(page):
    print("\n[TEST] Click 'All' button...")
    all_button = find_all_button(page)
    check(all_button.count() > 0, "'All' button found")
    all_button.click()
    pause(2)
    active_button = page.locator(".btn-group .btn-primary")
    button_text = active_button.first.text_content()
    check(
        "All" in (button_text or "") or "全部" in (button_text or ""),
        f"'All' button is now active (text: '{button_text}')",
    )
    shot(page, "04-all-button-active")


def test_all_button_date_range(page):
    """Verify 'All' shows the actual data range (not a hardcoded window)."""
    global captured_data_range
    print("\n[TEST] Verify 'All' button date range...")
    start_input = page.locator("input[type='date']").first
    end_input = page.locator("input[type='date']").nth(1)
    start_value = start_input.input_value()
    end_value = end_input.input_value()
    print(f"    [INFO] Start date: {start_value}")
    print(f"    [INFO] End date: {end_value}")

    if captured_data_range and captured_data_range.get("min_date"):
        check(
            start_value == captured_data_range["min_date"],
            f"Start equals data_range.min_date ({start_value} vs {captured_data_range['min_date']})",
        )
        check(
            end_value == captured_data_range["max_date"],
            f"End equals data_range.max_date ({end_value} vs {captured_data_range['max_date']})",
        )
    else:
        # Empty database: the page falls back to 365 days ago -> today.
        today = datetime.now().strftime("%Y-%m-%d")
        fallback_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        check(
            end_value == today,
            f"Empty DB fallback: end is today ({end_value} vs {today})",
        )
        check(
            start_value == fallback_start,
            f"Empty DB fallback: start is 365 days ago ({start_value} vs {fallback_start})",
        )

    start_date_obj = datetime.strptime(start_value, "%Y-%m-%d")
    check(start_date_obj <= datetime.now(), "Start date is not in the future")
    shot(page, "05-all-date-range")


def test_data_range_api(page):
    """Trigger and capture /api/analysis/data-range."""
    global captured_data_range
    print("\n[TEST] /api/analysis/data-range endpoint...")

    def handle_response(response):
        if "/api/analysis/data-range" in response.url:
            try:
                body = response.json()
                if body and isinstance(body, dict):
                    globals()["captured_data_range"] = body
                    print(f"    [INFO] data_range: {body}")
            except Exception:
                pass

    page.on("response", handle_response)

    # Re-trigger by toggling to 30 days then back to All
    thirty = find_button_by_text(page, "30")
    if thirty.count() > 0:
        thirty.click()
        pause(1)
    find_all_button(page).click()
    pause(2)

    captured_data_range = globals().get("captured_data_range")
    if captured_data_range:
        check("min_date" in captured_data_range, "data_range contains min_date")
        check("max_date" in captured_data_range, "data_range contains max_date")
    else:
        print("    [INFO] No data_range captured (database may be empty -> null response)")
    shot(page, "06-api-data-range")


def test_manual_date_transition_overwrites(page):
    """Editing a date while in 7/30/90 switches to 'all'; data range then applies."""
    print("\n[TEST] Manual edit from 30 -> 'all' is overwritten by data range...")
    thirty = find_button_by_text(page, "30")
    if thirty.count() > 0:
        thirty.click()
        pause(1)
    active = page.locator(".btn-group .btn-primary").first.text_content()
    check("30" in (active or ""), "'30' active before manual edit")

    start_input = page.locator("input[type='date']").first
    new_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    start_input.fill(new_date)
    pause(2)

    active = page.locator(".btn-group .btn-primary").first.text_content()
    check(
        "All" in (active or "") or "全部" in (active or ""),
        f"After manual edit, 'All' becomes active (text: '{active}')",
    )

    if globals().get("captured_data_range") and globals()["captured_data_range"].get("min_date"):
        final_start = start_input.input_value()
        check(
            final_start == globals()["captured_data_range"]["min_date"],
            "Transition into 'all' overwrites manual input with data_range.min_date",
        )
    shot(page, "07-manual-transition")


def test_manual_edit_within_all_preserved(page):
    """Once already in 'all', a manual edit is preserved (quickRange unchanged)."""
    print("\n[TEST] Manual edit within 'all' is preserved...")
    find_all_button(page).click()
    pause(2)
    start_input = page.locator("input[type='date']").first
    new_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    start_input.fill(new_date)
    pause(1)
    final_start = start_input.input_value()
    check(
        final_start == new_date, f"Manual edit within 'all' preserved ({final_start} vs {new_date})"
    )
    shot(page, "08-manual-within-all")


def run_tests():
    global passed, failed, errors
    print("=" * 60)
    print("Anomaly Detection 'All' Button E2E Tests")
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        try:
            login(page)
            navigate_to_anomaly(page)
            test_default_date_range(page)
            test_all_button_click(page)
            test_data_range_api(page)
            test_all_button_date_range(page)
            test_manual_date_transition_overwrites(page)
            test_manual_edit_within_all_preserved(page)
        except Exception as e:
            print(f"\n[ERROR] Test execution failed: {e}")
            shot(page, "error-state")
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
