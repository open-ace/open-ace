#!/usr/bin/env python3
"""
Test Issue 48: Status bar shows today's usage and quota info

Test cases:
1. Navigate to Work mode
2. Check status bar displays Token usage/quota
3. Check status bar displays Request usage/quota
4. Verify progress bars for both metrics
"""

import os
import sys
import time

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, project_root)

from playwright.sync_api import expect, sync_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
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


def test_status_bar_usage():
    """Test status bar displays today's usage and quota."""
    screenshots = []

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
            print("\nStep 3: Switch to Work mode")
            # Click Work mode button in sidebar
            work_mode_btn = page.locator(".mode-switcher .mode-btn").first
            if work_mode_btn.is_visible():
                work_mode_btn.click()
                time.sleep(2)
                page.wait_for_load_state("networkidle")
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

            # Check token progress bar
            token_progress = token_usage.locator(".status-progress-bar")
            expect(token_progress).to_be_visible()
            print("  ✓ Token progress bar is visible")

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

            # Check request progress bar
            request_progress = request_usage.locator(".status-progress-bar")
            expect(request_progress).to_be_visible()
            print("  ✓ Request progress bar is visible")

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


if __name__ == "__main__":
    test_status_bar_usage()
