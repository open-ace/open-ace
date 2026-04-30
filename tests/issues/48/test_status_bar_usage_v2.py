#!/usr/bin/env python3
"""
Test Issue 48 - Part 2: Status bar improvements

Test cases:
1. Navigate to Work mode
2. Check status bar displays Token usage/quota (localized)
3. Check status bar displays Request usage/quota (localized)
4. Verify progress bars for both metrics
5. Verify no model/GPT-4 display
6. Verify no latency display
7. Verify new icons (bar-chart and arrow-up-circle)
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


def test_status_bar_improvements():
    """Test status bar improvements for Issue 48."""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        print("=" * 60)
        print("Testing Issue 48 Part 2: Status Bar Improvements")
        print("=" * 60)

        try:
            # Step 1: Navigate to login page
            print("\nStep 1: Navigate to login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "v2_01_login_page.png")

            # Step 2: Login
            print("\nStep 2: Login as admin")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            take_screenshot(page, "v2_02_after_login.png")

            # Step 3: Switch to Work mode
            print("\nStep 3: Switch to Work mode")
            work_mode_btn = page.locator(".mode-switcher .mode-btn").first
            if work_mode_btn.is_visible():
                work_mode_btn.click()
                time.sleep(2)
                page.wait_for_load_state("networkidle")
            take_screenshot(page, "v2_03_work_mode.png")

            # Step 4: Check status bar exists
            print("\nStep 4: Check status bar exists")
            status_bar = page.locator(".work-status-bar")
            expect(status_bar).to_be_visible()
            print("  ✓ Status bar is visible")
            take_screenshot(page, "v2_04_status_bar.png")

            # Step 5: Verify no model/GPT-4 display
            print("\nStep 5: Verify no model/GPT-4 display")
            model_display = page.locator(".status-model")
            count = model_display.count()
            assert count == 0, f"Model display should not exist, found {count}"
            print("  ✓ No model/GPT-4 display")

            # Step 6: Verify no latency display
            print("\nStep 6: Verify no latency display")
            latency_display = page.locator(".status-latency")
            count = latency_display.count()
            assert count == 0, f"Latency display should not exist, found {count}"
            print("  ✓ No latency display")

            # Step 7: Check Token usage display
            print("\nStep 7: Check Token usage display")
            token_usage = page.locator(".status-token-usage")
            expect(token_usage).to_be_visible()
            print("  ✓ Token usage element is visible")

            # Check token icon is bi-bar-chart
            token_icon = token_usage.locator("i")
            icon_class = token_icon.get_attribute("class")
            assert (
                "bi-bar-chart" in icon_class
            ), f"Token icon should be bi-bar-chart, got {icon_class}"
            print("  ✓ Token icon is bi-bar-chart")

            # Check token label (should be localized "Token:")
            token_label = token_usage.locator(".status-label")
            label_text = token_label.text_content()
            assert (
                "Token" in label_text or "トークン" in label_text or "토큰" in label_text
            ), f"Token label should be localized, got {label_text}"
            print(f"  ✓ Token label is localized: '{label_text}'")

            # Check token progress bar (may not be visible if usage is 0)
            token_progress_container = token_usage.locator(".status-progress")
            expect(token_progress_container).to_be_visible()
            print("  ✓ Token progress bar container is visible")

            # Step 8: Check separator
            print("\nStep 8: Check separator between Token and Request")
            separator = page.locator(".status-separator")
            expect(separator).to_be_visible()
            separator_text = separator.text_content()
            assert separator_text == "|", f"Separator should be '|', got '{separator_text}'"
            print("  ✓ Separator is '|' symbol")

            # Step 9: Check Request usage display
            print("\nStep 9: Check Request usage display")
            request_usage = page.locator(".status-request-usage")
            expect(request_usage).to_be_visible()
            print("  ✓ Request usage element is visible")

            # Check request icon is bi-arrow-up-circle
            request_icon = request_usage.locator("i")
            icon_class = request_icon.get_attribute("class")
            assert (
                "bi-arrow-up-circle" in icon_class
            ), f"Request icon should be bi-arrow-up-circle, got {icon_class}"
            print("  ✓ Request icon is bi-arrow-up-circle")

            # Check request label (should be localized)
            request_label = request_usage.locator(".status-label")
            label_text = request_label.text_content()
            assert (
                "Request" in label_text
                or "请求" in label_text
                or "リクエスト" in label_text
                or "요청" in label_text
            ), f"Request label should be localized, got {label_text}"
            print(f"  ✓ Request label is localized: '{label_text}'")

            # Check request progress bar (may not be visible if usage is 0)
            request_progress_container = request_usage.locator(".status-progress")
            expect(request_progress_container).to_be_visible()
            print("  ✓ Request progress bar container is visible")

            # Final screenshot
            take_screenshot(page, "v2_05_final_status_bar.png")

            print("\n" + "=" * 60)
            print("Test Result: PASSED ✓")
            print("=" * 60)
            print("\nAll test steps completed successfully:")
            print("  ✓ Status bar is visible in Work mode")
            print("  ✓ No model/GPT-4 display")
            print("  ✓ No latency display")
            print("  ✓ Token usage with bar-chart icon")
            print("  ✓ Request usage with arrow-up-circle icon")
            print("  ✓ Labels are localized")
            print("  ✓ Progress bars for both metrics visible")
            print("  ✓ Separator '|' between Token and Request")

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            take_screenshot(page, "v2_error_state.png")
            raise

        finally:
            browser.close()


if __name__ == "__main__":
    test_status_bar_improvements()
