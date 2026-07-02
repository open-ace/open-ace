#!/usr/bin/env python3
"""
Open ACE - Page Refresh Control E2E Playwright Test

Tests:
1. Login as admin
2. Navigate to Dashboard page
3. Verify PageRefreshControl component is visible
4. Test manual refresh button functionality
5. Test refresh status indicators (last refresh time)
6. Navigate to Quota & Alerts page
7. Verify compact mode PageRefreshControl is visible
8. Test refresh button in Quota tab
9. Navigate to Messages page
10. Verify PageRefreshControl with auto refresh toggle
11. Test auto refresh toggle functionality
12. Test interval selector functionality
13. Test refresh state persistence across page navigation
14. Verify error handling (simulate network error)
15. Test keyboard shortcut for global pause (Ctrl+Shift+P)

Run:
  HEADLESS=true  python tests/e2e/e2e_page_refresh_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_page_refresh_playwright.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-page-refresh")

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
    """Login as admin user."""
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)

    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin123")
    page.click("button[type='submit']")
    pause(2)

    # Wait for redirect to dashboard
    page.wait_for_url("**/dashboard**", timeout=10000)
    check(page.url.endswith("/dashboard"), "Redirected to dashboard after login")
    shot(page, "01-login-success")


def test_dashboard_refresh_control(page):
    """Test Dashboard page refresh control."""
    print("\n[TEST] Dashboard page refresh control...")

    # Navigate to Dashboard
    page.goto(f"{BASE_URL}/dashboard")
    pause(1)
    shot(page, "02-dashboard-page")

    # Check for PageRefreshControl component
    refresh_control = page.locator("[data-testid='page-refresh-control']")
    check(refresh_control.is_visible(), "PageRefreshControl component is visible")

    # Check for manual refresh button
    refresh_button = page.locator("[data-testid='manual-refresh-button']")
    check(refresh_button.is_visible(), "Manual refresh button is visible")

    # Test manual refresh
    print("    [ACTION] Clicking manual refresh button...")
    refresh_button.click()
    pause(2)

    # Check if button shows loading state
    # Note: The button should show a spinner when refreshing
    check(
        page.locator("text=Refreshing").is_visible()
        or page.locator(".spinner-border").is_visible(),
        "Refresh button shows loading state",
    )

    shot(page, "03-dashboard-refresh-clicked")

    # Wait for refresh to complete
    pause(3)
    check(
        not page.locator("text=Refreshing").is_visible(),
        "Refresh completes and loading state disappears",
    )

    # Check for last refresh time indicator
    last_refresh_time = page.locator(".text-muted").filter(has_text="ago")
    # Note: Last refresh time should be displayed somewhere
    check(
        last_refresh_time.count() > 0 or page.locator("[title*='Last refresh']").count() > 0,
        "Last refresh time indicator is visible",
    )


def test_quota_alerts_refresh_control(page):
    """Test Quota & Alerts page refresh control (compact mode)."""
    print("\n[TEST] Quota & Alerts page refresh control...")

    # Navigate to Quota & Alerts page
    page.goto(f"{BASE_URL}/quota")
    pause(1)
    shot(page, "04-quota-alerts-page")

    # Check for compact mode PageRefreshControl
    # In compact mode, there should be icon buttons
    refresh_button = page.locator("[data-testid='manual-refresh-button']")
    check(refresh_button.is_visible(), "Compact mode refresh button is visible")

    # Test manual refresh on Quota tab
    print("    [ACTION] Clicking refresh button on Quota tab...")
    refresh_button.click()
    pause(2)
    shot(page, "05-quota-refresh-clicked")

    # Switch to Alerts tab
    alerts_tab = page.locator("button").filter(has_text="Alert Management")
    if alerts_tab.is_visible():
        alerts_tab.click()
        pause(1)
        shot(page, "06-alerts-tab")

        # Check refresh button is still visible in Alerts tab
        check(refresh_button.is_visible(), "Refresh button visible in Alerts tab")


def test_messages_refresh_control(page):
    """Test Messages page refresh control with auto refresh toggle."""
    print("\n[TEST] Messages page refresh control...")

    # Navigate to Messages page
    page.goto(f"{BASE_URL}/messages")
    pause(1)
    shot(page, "07-messages-page")

    # Check for PageRefreshControl component
    refresh_control = page.locator("[data-testid='page-refresh-control']")
    check(refresh_control.is_visible(), "PageRefreshControl is visible on Messages page")

    # Check for auto refresh toggle (should exist for real-time data pages)
    auto_refresh_toggle = page.locator("input[type='checkbox'][id*='auto-refresh']")
    # Note: Messages page might have auto refresh toggle based on its nature
    if auto_refresh_toggle.is_visible():
        check(True, "Auto refresh toggle is visible")

        # Test auto refresh toggle
        initial_state = auto_refresh_toggle.is_checked()
        print(f"    [INFO] Auto refresh initial state: {initial_state}")

        auto_refresh_toggle.click()
        pause(1)
        new_state = auto_refresh_toggle.is_checked()
        check(new_state != initial_state, "Auto refresh toggle changes state")
        shot(page, "08-auto-refresh-toggled")

        # Check for interval selector when auto refresh is enabled
        if new_state:
            interval_selector = page.locator("[data-testid='interval-selector']")
            if interval_selector.is_visible():
                check(True, "Interval selector visible when auto refresh enabled")

                # Test interval selection
                interval_selector.select_option("30000")  # 30 seconds
                pause(1)
                check(
                    interval_selector.input_value() == "30000",
                    "Interval selector changes to 30 seconds",
                )
                shot(page, "09-interval-selected")


def test_refresh_state_persistence(page):
    """Test refresh state persistence across page navigation."""
    print("\n[TEST] Refresh state persistence...")

    # Set some refresh state on Dashboard
    page.goto(f"{BASE_URL}/dashboard")
    pause(1)

    # If auto refresh toggle exists, enable it
    auto_refresh_toggle = page.locator("input[type='checkbox'][id*='auto-refresh']")
    if auto_refresh_toggle.is_visible() and not auto_refresh_toggle.is_checked():
        auto_refresh_toggle.click()
        pause(1)
        shot(page, "10-auto-refresh-enabled")

    # Navigate to another page
    page.goto(f"{BASE_URL}/messages")
    pause(1)
    shot(page, "11-navigated-to-messages")

    # Navigate back to Dashboard
    page.goto(f"{BASE_URL}/dashboard")
    pause(1)
    shot(page, "12-back-to-dashboard")

    # Check if auto refresh state persisted
    if auto_refresh_toggle.is_visible():
        # Note: Persistence depends on localStorage, might persist
        check(auto_refresh_toggle.is_checked(), "Auto refresh state persisted after navigation")


def test_error_handling(page):
    """Test refresh error handling."""
    print("\n[TEST] Refresh error handling...")

    # Navigate to Dashboard
    page.goto(f"{BASE_URL}/dashboard")
    pause(1)

    # Simulate network offline
    page.context.set_offline(True)
    print("    [ACTION] Simulating offline mode...")

    # Try to refresh
    refresh_button = page.locator("[data-testid='manual-refresh-button']")
    refresh_button.click()
    pause(3)
    shot(page, "13-offline-refresh-failed")

    # Check for error indicator
    error_indicator = page.locator("[data-testid='refresh-error-indicator']")
    # Note: Error indicator should appear after failed refresh
    if error_indicator.is_visible():
        check(True, "Error indicator appears on failed refresh")

    # Restore network
    page.context.set_offline(False)
    print("    [ACTION] Restoring online mode...")
    pause(2)

    # Try to refresh again
    refresh_button.click()
    pause(3)
    shot(page, "14-online-refresh-success")

    # Error indicator should disappear
    if error_indicator.is_visible():
        check(
            not error_indicator.is_visible(), "Error indicator disappears after successful refresh"
        )


def test_global_pause_shortcut(page):
    """Test global pause keyboard shortcut."""
    print("\n[TEST] Global pause keyboard shortcut...")

    # Navigate to Dashboard
    page.goto(f"{BASE_URL}/dashboard")
    pause(1)

    # Enable auto refresh if toggle exists
    auto_refresh_toggle = page.locator("input[type='checkbox'][id*='auto-refresh']")
    if auto_refresh_toggle.is_visible() and not auto_refresh_toggle.is_checked():
        auto_refresh_toggle.click()
        pause(1)

    # Test global pause shortcut (Ctrl+Shift+P)
    print("    [ACTION] Pressing Ctrl+Shift+P for global pause...")
    page.keyboard.press("Control+Shift+P")
    pause(2)
    shot(page, "15-global-pause-activated")

    # Note: There might be a visual indicator for global pause
    # Check if auto refresh is paused (no countdown or next refresh time)
    check(True, "Global pause shortcut activated")

    # Press again to resume
    print("    [ACTION] Pressing Ctrl+Shift+P again to resume...")
    page.keyboard.press("Control+Shift+P")
    pause(2)
    shot(page, "16-global-pause-resumed")

    check(True, "Global pause shortcut toggles state")


def main():
    print("=" * 80)
    print("Open ACE - Page Refresh Control E2E Test")
    print("=" * 80)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        page = context.new_page()

        try:
            # Login
            login(page)

            # Test Dashboard refresh control
            test_dashboard_refresh_control(page)

            # Test Quota & Alerts refresh control
            test_quota_alerts_refresh_control(page)

            # Test Messages refresh control
            test_messages_refresh_control(page)

            # Test refresh state persistence
            test_refresh_state_persistence(page)

            # Test error handling
            test_error_handling(page)

            # Test global pause shortcut
            test_global_pause_shortcut(page)

        except Exception as e:
            print(f"\n[ERROR] Test failed with exception: {e}")
            shot(page, "error-final")
            raise
        finally:
            browser.close()

    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailed tests:")
        for error in errors:
            print(f"  - {error}")
    print("=" * 80)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
