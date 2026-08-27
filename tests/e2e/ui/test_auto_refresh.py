"""
Test script for Issue #47: Claude 工具的 messages 在 auto-refresh 时不能及时显示

This test drives the Messages page (/manage/messages) through the
auto-refresh toggle and the date-range picker:
1. Toggling auto-refresh on/off via the PageRefreshControl dropdown keeps
   the page responsive and the toggle state intact.
2. Selecting a historical date through the DatePicker popup keeps the
   filtered view stable across an auto-refresh cycle.

#2457 realignment: converted from the async playwright API (the sync `with`
on async_playwright() was a protocol error — the baselined TypeError) and
re-pointed at the current Messages page (/manage/messages): PageRefreshControl
carries the auto-refresh toggle, the date range moved into
.messages-filter-dates inputs, and the admin password-change gate is cleared
like every other lane e2e. The sync API also keeps pytest away from the
async teardown path entirely.
"""

import os
import re
import time
from datetime import datetime, timedelta

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(47)]


HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000  # 15 seconds timeout


def _clear_seeded_password_gate():
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


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _login(page):
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type='submit']")
    # admins land on /manage, users on /work
    page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
    page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle")
    page.wait_for_selector(".messages", state="visible", timeout=TIMEOUT)


def _enable_auto_refresh(page):
    """Open PageRefreshControl's dropdown and tick the auto-refresh item."""
    page.locator("[data-testid='dropdown-toggle']").first.click()
    checkbox = page.locator("[id$='-auto-refresh']").first
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.check()
    page.keyboard.press("Escape")
    return checkbox


def _disable_auto_refresh(page, checkbox):
    """Re-open the dropdown and untick (the menu closed on Escape)."""
    page.locator("[data-testid='dropdown-toggle']").first.click()
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.uncheck()
    page.keyboard.press("Escape")


def test_auto_refresh_today():
    """Test auto-refresh when viewing today's messages."""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    print("=" * 60)
    print("[Test 1] Auto-refresh when viewing today")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            _login(page)
            print("✓ Messages page loaded")

            # Date range lives in DatePicker buttons (custom input), not <input>s
            dates = page.locator(".messages-filter-dates button")
            values = [dates.nth(i).inner_text().strip() for i in range(min(dates.count(), 2))]
            print(f"  Date filter buttons: {dates.count()}, values: {values}")

            # Enable auto-refresh (PageRefreshControl dropdown item)
            print("\n[Step] Enabling auto-refresh...")
            auto_refresh_checkbox = _enable_auto_refresh(page)
            assert auto_refresh_checkbox.is_checked(), "auto-refresh checkbox did not get ticked"
            print("✓ Auto-refresh enabled")

            # Wait and observe one refresh cycle
            print("  Waiting for auto-refresh cycle (10 seconds)...")
            time.sleep(10)

            # Check the page is still responsive
            print("  Checking page responsiveness...")
            page.hover(".messages-header h2")
            assert page.locator(
                ".messages"
            ).is_visible(), "messages page stopped responding after an auto-refresh cycle"
            print("✓ Page is responsive after auto-refresh")

            _disable_auto_refresh(page, auto_refresh_checkbox)
            print("✓ Auto-refresh disabled")

            page.screenshot(path="screenshots/issues/47/test_auto_refresh_today.png")
            print("✓ Screenshot saved")
            print("Test 1 completed successfully!")

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            page.screenshot(path="screenshots/issues/47/test_auto_refresh_today_error.png")
            raise
        finally:
            browser.close()


def test_auto_refresh_historical_date():
    """Test auto-refresh when viewing a historical date."""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    print("\n" + "=" * 60)
    print("[Test 2] Auto-refresh when viewing historical date")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            _login(page)

            # Set the range to yesterday
            print("\n[Step] Setting date range to yesterday...")
            yesterday = datetime.now() - timedelta(days=1)
            # DatePickers render as buttons that open a react-datepicker popup
            page.locator(".messages-filter-dates button").first.click()
            page.wait_for_selector(".react-datepicker", timeout=5000)
            day_cell = page.locator(
                ".react-datepicker__day:not(.react-datepicker__day--outside-month)",
                has_text=str(yesterday.day),
            ).first
            day_cell.click()
            print(f"  Start date set to: {yesterday.strftime('%Y-%m-%d')}")

            # Wait for the filtered view to load
            time.sleep(2)
            assert page.locator(
                ".messages"
            ).is_visible(), "messages view did not load for the historical date"
            print("✓ Messages loaded for historical date")

            # Enable auto-refresh
            print("  Enabling auto-refresh...")
            auto_refresh_checkbox = _enable_auto_refresh(page)
            assert auto_refresh_checkbox.is_checked(), "auto-refresh checkbox did not get ticked"
            print("✓ Auto-refresh enabled")

            # Wait for the auto-refresh cycle; viewing a historical range must
            # not break the cycle or the page
            print("  Waiting for auto-refresh cycle (10 seconds)...")
            time.sleep(10)
            assert page.locator(
                ".messages"
            ).is_visible(), "messages page broke during auto-refresh on a historical date"
            print("✓ Auto-refresh cycle completed")

            _disable_auto_refresh(page, auto_refresh_checkbox)
            print("✓ Auto-refresh disabled")

            page.screenshot(path="screenshots/issues/47/test_auto_refresh_historical.png")
            print("✓ Screenshot saved")
            print("Test 2 completed successfully!")

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            page.screenshot(path="screenshots/issues/47/test_auto_refresh_historical_error.png")
            raise
        finally:
            browser.close()
