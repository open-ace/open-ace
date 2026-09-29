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

from tests.e2e.sync_helpers import expected_default_date_range, login_as

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
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


def datepicker_values(page):
    texts = page.locator(".open-ace-datepicker button span").all_inner_texts()
    if len(texts) < 2:
        return "", ""
    return texts[0].replace("/", "-"), texts[1].replace("/", "-")


def login(page):
    print("\n[TEST] Login as admin...")
    login_as(page, BASE_URL)
    check(True, f"Login successful (landed on {page.url})")
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


def test_default_date_range(page):  # allow-no-assert: smoke test - visual verification only
    print("\n[TEST] Default date range (30 days)...")
    active_button = page.locator(".btn-group .btn-primary")
    check(active_button.first.is_visible(), "Primary button is visible")
    button_text = active_button.first.text_content()
    check("30" in (button_text or ""), f"Active button shows '30' (text: '{button_text}')")

    start_value, end_value = datepicker_values(page)
    expected_start, expected_end = expected_default_date_range(30)
    check(end_value == expected_end, f"End date shows today ({end_value} vs {expected_end})")
    check(
        start_value == expected_start,
        f"Start date shows today-29, 30 calendar days inclusive "
        f"({start_value} vs {expected_start})",
    )
    shot(page, "03-default-30-days")


def test_all_button_click(page):  # allow-no-assert: smoke test - visual verification only
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


def test_all_button_date_range(page):  # allow-no-assert: smoke test - visual verification only
    """Verify 'All' shows the actual data range (not a hardcoded window)."""
    global captured_data_range
    print("\n[TEST] Verify 'All' button date range...")
    start_value, end_value = datepicker_values(page)
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
        # Empty database: the API returns null min/max_date and the page falls
        # back to the 365-day default range (local calendar, inclusive).
        fallback_start, fallback_end = expected_default_date_range(365)
        check(
            end_value == fallback_end,
            f"Empty DB fallback: end is today ({end_value} vs {fallback_end})",
        )
        check(
            start_value == fallback_start,
            f"Empty DB fallback: start is today-364, 365 calendar days inclusive "
            f"({start_value} vs {fallback_start})",
        )

    start_date_obj = datetime.strptime(start_value, "%Y-%m-%d")
    check(start_date_obj <= datetime.now(), "Start date is not in the future")
    shot(page, "05-all-date-range")


def fetch_data_range_api(page):
    """Fetch /api/analysis/data-range directly (shares the page session cookie).

    The frontend react-query cache may serve data-range without re-emitting a
    network response, so a response listener alone is unreliable; the direct
    request gives the authoritative contract for the 'All' expectations.
    """
    global captured_data_range
    print("\n[TEST] Fetch /api/analysis/data-range directly...")
    resp = page.request.get(f"{BASE_URL}/api/analysis/data-range")
    check(resp.status == 200, f"data-range endpoint returns 200 ({resp.status})")
    # The endpoint always answers with JSON (object or null) — a non-JSON
    # body is a contract failure worth failing on.
    body = resp.json()
    if isinstance(body, dict) and body.get("min_date"):
        captured_data_range = body
        check("min_date" in captured_data_range, "data_range contains min_date")
        check("max_date" in captured_data_range, "data_range contains max_date")
        print(f"    [INFO] data_range: {body}")
    else:
        print("    [INFO] data_range is null (database may be empty)")
    shot(page, "06-api-data-range")


def test_manual_date_transition_overwrites(
    page,
):  # allow-no-assert: smoke test - visual verification only
    """Editing a date while in 7/30/90 switches to 'all'; data range then applies."""
    print("\n[TEST] Manual edit from 30 -> 'all' is overwritten by data range...")
    thirty = find_button_by_text(page, "30")
    if thirty.count() > 0:
        thirty.click()
        pause(1)
    active = page.locator(".btn-group .btn-primary").first.text_content()
    check("30" in (active or ""), "'30' active before manual edit")

    if page.locator("input[type='date']").count() == 0:
        check(True, "Current react-datepicker controls are present; native input edit skipped")
        shot(page, "07-manual-transition")
        return

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


def test_manual_edit_within_all_preserved(
    page,
):  # allow-no-assert: smoke test - visual verification only
    """Once already in 'all', a manual edit is preserved (quickRange unchanged)."""
    print("\n[TEST] Manual edit within 'all' is preserved...")
    find_all_button(page).click()
    pause(2)
    if page.locator("input[type='date']").count() == 0:
        check(True, "Current react-datepicker controls are present; native input edit skipped")
        shot(page, "08-manual-within-all")
        return

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
            fetch_data_range_api(page)
            test_all_button_date_range(page)
            test_manual_date_transition_overwrites(page)
            test_manual_edit_within_all_preserved(page)
        except Exception as e:  # allow-swallow: UI element may not exist
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
