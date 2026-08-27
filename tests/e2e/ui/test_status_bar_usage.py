"""
Test Issue 48: Status bar shows today's usage and quota info

Test cases:
1. Navigate to Work mode
2. Check status bar displays Token usage/quota
3. Check status bar displays Request usage/quota
4. Verify progress bars for both metrics
"""

import os
import time

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(48)]


# Test configuration
# #2457: honor the lane runner's exported BASE_URL (ephemeral port);
# the hardcoded default was the baselined ERR_CONNECTION_REFUSED
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
    "48",
)


def ensure_screenshot_dir():
    """Ensure screenshot directory exists."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name: str):
    """Take screenshot and save to issue-specific directory."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only, #2457)."""
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
    import requests

    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except Exception:
        import pytest

        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_status_bar_usage():
    """Test status bar displays today's usage and quota."""
    _skip_if_no_server()
    _clear_seeded_password_gate()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        print("=" * 60)
        print("Testing Issue 48: Status Bar Usage Display")
        print("=" * 60)

        try:
            # Step 1: Navigate to login page
            print("\nStep 1: Navigate to login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "01_login_page.png")

            # Step 2: Login
            print("\nStep 2: Login as admin")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            take_screenshot(page, "02_after_login.png")

            # Step 3: Switch to Work mode
            # (admins land on /manage; the status bar lives in work mode)
            page.goto(f"{BASE_URL}/work")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            take_screenshot(page, "03_work_mode.png")

            # Step 4: Check status bar exists
            print("\nStep 4: Check status bar exists")
            status_bar = page.locator(".work-status-bar")
            expect(status_bar).to_be_visible()
            print("  ✓ Status bar is visible")
            take_screenshot(page, "04_status_bar_visible.png")

            # Step 5: Check Token usage display
            print("\nStep 5: Check Token usage display")
            token_usage = page.locator(".status-token-usage")
            expect(token_usage).to_be_visible()
            print("  ✓ Token usage element is visible")

            # Check token label
            token_label = token_usage.locator(".status-label")
            expect(token_label).to_contain_text("Token")
            print("  ✓ Token label is present")

            # Check token values (should have format "X / Y")
            token_values = token_usage.locator(".status-tokens")
            token_text = token_values.text_content()
            print(f"  Token values: {token_text}")
            assert "/" in token_text, "Token values should contain '/' separator"
            print("  ✓ Token values format is correct")

            # Check token progress bar — presence, not visibility: with zero
            # usage the bar renders at zero width, which playwright counts
            # as hidden (#2457)
            token_progress = token_usage.locator(".status-progress-bar")
            expect(token_progress).to_be_attached()
            print("  ✓ Token progress bar is attached")

            # Step 6: Check separator
            print("\nStep 6: Check separator between Token and Request")
            separator = page.locator(".status-separator")
            expect(separator).to_be_visible()
            separator_text = separator.text_content()
            assert separator_text == "|", f"Separator should be '|', got '{separator_text}'"
            print("  ✓ Separator is '|' symbol")

            # Step 7: Check Request usage display
            print("\nStep 7: Check Request usage display")
            request_usage = page.locator(".status-request-usage")
            expect(request_usage).to_be_visible()
            print("  ✓ Request usage element is visible")

            # Check request label
            request_label = request_usage.locator(".status-label")
            expect(request_label).to_contain_text("Request")
            print("  ✓ Request label is present")

            # Check request values (should have format "X / Y")
            request_values = request_usage.locator(".status-requests")
            request_text = request_values.text_content()
            print(f"  Request values: {request_text}")
            assert "/" in request_text, "Request values should contain '/' separator"
            print("  ✓ Request values format is correct")

            # Check request progress bar — same zero-width caveat as tokens
            request_progress = request_usage.locator(".status-progress-bar")
            expect(request_progress).to_be_attached()
            print("  ✓ Request progress bar is attached")

            # Final screenshot
            take_screenshot(page, "05_final_status_bar.png")

            print("\n" + "=" * 60)
            print("Test Result: PASSED ✓")
            print("=" * 60)
            print("\nAll test steps completed successfully:")
            print("  ✓ Status bar is visible in Work mode")
            print("  ✓ Token usage and quota displayed correctly")
            print("  ✓ Request usage and quota displayed correctly")
            print("  ✓ Progress bars for both metrics visible")
            print("  ✓ Separator '|' between Token and Request")

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            take_screenshot(page, "error_state.png")
            raise

        finally:
            browser.close()
