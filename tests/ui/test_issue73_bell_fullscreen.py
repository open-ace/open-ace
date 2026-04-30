#!/usr/bin/env python3
"""
UI Test - Issue #73: Bell button missing in fullscreen mode and tooltip text abnormal

Tests:
1. Non-fullscreen mode: bell button exists and has correct tooltip text
2. Fullscreen mode: bell button should exist (not missing)
3. Bell button tooltip text should be meaningful (not translation key)

Screenshots: screenshots/issues/73/
"""

import sys
import os
import time

# Add skill scripts to path
skill_dir = '/Users/rhuang/workspace/open-ace/.qwen/skills/ui-test/scripts'
if os.path.exists(skill_dir):
    sys.path.insert(0, skill_dir)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
HEADLESS = True
VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/73"

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def take_screenshot(page, name):
    """Take screenshot and save to screenshot directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=False)
    print(f"  Screenshot saved: {path}")
    return path

def login(page):
    """Login to the system"""
    print("  Logging in...")
    page.goto(f"{BASE_URL}/login")
    page.wait_for_selector("#username", timeout=10000)
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type='submit']")
    # Wait for redirect to home
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)
    time.sleep(2)

def test_issue73():
    """Test Issue #73: Bell button in fullscreen mode"""
    screenshots = []
    test_results = []

    print("\n========================================")
    print("Issue #73 Test: Bell button in fullscreen mode")
    print("========================================")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\nStep 1: Login")
            login(page)
            screenshots.append(take_screenshot(page, "01_login.png"))
            test_results.append(("Login", "PASS"))

            # Step 2: Navigate to Workspace
            print("\nStep 2: Navigate to Workspace")
            page.goto(f"{BASE_URL}/work/workspace")
            page.wait_for_load_state("networkidle")
            time.sleep(3)  # Wait for workspace to load
            screenshots.append(take_screenshot(page, "02_workspace.png"))

            # Check if workspace loaded
            workspace = page.locator(".workspace")
            if workspace.count() > 0:
                print("  ✓ Workspace page loaded")
                test_results.append(("Workspace Load", "PASS"))
            else:
                print("  ✗ Workspace page not loaded")
                test_results.append(("Workspace Load", "FAIL"))
                return False

            # Step 3: Check bell button in non-fullscreen mode
            print("\nStep 3: Check bell button in non-fullscreen mode")

            # Look for the notification toggle button in page-header
            # It should have bi-bell or bi-bell-slash icon
            bell_btn = page.locator("button:has(.bi-bell), button:has(.bi-bell-slash), button:has(.bi-bell-fill)").first

            # More specific selector for the notification toggle button
            bell_btn_header = page.locator(".page-header button:has(.bi-bell), .page-header button:has(.bi-bell-slash), .page-header button:has(.bi-bell-fill)")

            if bell_btn_header.count() > 0:
                print("  ✓ Bell button found in page-header (non-fullscreen mode)")
                test_results.append(("Bell Button Non-Fullscreen", "PASS"))

                # Get tooltip text
                tooltip_text = bell_btn_header.first.get_attribute("title")
                print(f"  Tooltip text: '{tooltip_text}'")

                # Check if tooltip is a valid translation (not a key like 'enableTabNotifications')
                if tooltip_text and not tooltip_text.startswith("enable") and not tooltip_text.startswith("disable"):
                    if "notification" in tooltip_text.lower() or "通知" in tooltip_text:
                        print("  ✓ Tooltip text is meaningful")
                        test_results.append(("Tooltip Text Valid", "PASS", tooltip_text))
                    else:
                        print(f"  ? Tooltip text: '{tooltip_text}'")
                        test_results.append(("Tooltip Text", "INFO", tooltip_text))
                elif tooltip_text and (tooltip_text.startswith("enable") or tooltip_text.startswith("disable")):
                    # Check if it's a translation key (bad)
                    if "TabNotifications" in tooltip_text:
                        print("  ✗ Tooltip text is a translation key, not translated text")
                        test_results.append(("Tooltip Text Valid", "FAIL", f"Key: {tooltip_text}"))
                    else:
                        print(f"  ✓ Tooltip text looks valid: '{tooltip_text}'")
                        test_results.append(("Tooltip Text Valid", "PASS", tooltip_text))
                else:
                    print(f"  ? No tooltip or empty: '{tooltip_text}'")
                    test_results.append(("Tooltip Text", "WARN", tooltip_text or "empty"))
            else:
                print("  ✗ Bell button NOT found in page-header")
                test_results.append(("Bell Button Non-Fullscreen", "FAIL"))

            screenshots.append(take_screenshot(page, "03_non_fullscreen_bell.png"))

            # Step 4: Enter fullscreen mode
            print("\nStep 4: Enter fullscreen mode")

            # Find fullscreen toggle button
            fullscreen_btn = page.locator(".fullscreen-toggle-btn, button:has(.bi-fullscreen)").first
            if fullscreen_btn.count() > 0:
                print("  Found fullscreen toggle button")
                fullscreen_btn.click()
                time.sleep(1)
                screenshots.append(take_screenshot(page, "04_fullscreen_mode.png"))

                # Verify fullscreen mode
                workspace_fs = page.locator(".workspace.fullscreen-mode, .workspace .fullscreen-mode")
                if workspace_fs.count() > 0:
                    print("  ✓ Fullscreen mode activated")
                    test_results.append(("Fullscreen Mode", "PASS"))
                else:
                    # Alternative check - page-header should be hidden
                    page_header = page.locator(".page-header.d-none")
                    if page_header.count() > 0:
                        print("  ✓ Fullscreen mode activated (page-header hidden)")
                        test_results.append(("Fullscreen Mode", "PASS"))
                    else:
                        print("  ? Fullscreen mode status unclear")
                        test_results.append(("Fullscreen Mode", "WARN"))
            else:
                print("  ✗ Fullscreen button not found")
                test_results.append(("Fullscreen Button", "FAIL"))

            # Step 5: Check bell button in fullscreen mode (CRITICAL TEST)
            print("\nStep 5: Check bell button in fullscreen mode")

            # In fullscreen mode, the bell button should be in workspace-tabs
            # NOT in page-header (which is hidden)
            bell_btn_fullscreen = page.locator(".workspace-tabs button:has(.bi-bell), .workspace-tabs button:has(.bi-bell-slash), .workspace-tabs button:has(.bi-bell-fill)")

            if bell_btn_fullscreen.count() > 0:
                print("  ✓ Bell button found in fullscreen mode (workspace-tabs)")
                test_results.append(("Bell Button Fullscreen", "PASS"))

                # Get tooltip text in fullscreen mode
                tooltip_fs = bell_btn_fullscreen.first.get_attribute("title")
                print(f"  Tooltip text (fullscreen): '{tooltip_fs}'")

                # Check tooltip validity
                if tooltip_fs and not ("TabNotifications" in tooltip_fs and not "通知" in tooltip_fs):
                    print("  ✓ Tooltip text is valid in fullscreen mode")
                    test_results.append(("Tooltip Fullscreen Valid", "PASS", tooltip_fs))
                elif tooltip_fs and "TabNotifications" in tooltip_fs:
                    print("  ✗ Tooltip text is a translation key in fullscreen mode")
                    test_results.append(("Tooltip Fullscreen Valid", "FAIL", tooltip_fs))
                else:
                    print(f"  ? Tooltip: '{tooltip_fs}'")
                    test_results.append(("Tooltip Fullscreen", "INFO", tooltip_fs or "empty"))
            else:
                print("  ✗ Bell button NOT found in fullscreen mode - THIS IS THE BUG!")
                test_results.append(("Bell Button Fullscreen", "FAIL"))

                # Take screenshot to show the missing button
                screenshots.append(take_screenshot(page, "05_fullscreen_no_bell.png"))

            screenshots.append(take_screenshot(page, "06_fullscreen_bell_check.png"))

            # Step 6: Verify both buttons exist (fullscreen + bell)
            print("\nStep 6: Verify buttons in fullscreen mode")
            exit_btn = page.locator(".workspace-tabs button:has(.bi-fullscreen-exit)")
            if exit_btn.count() > 0:
                print("  ✓ Exit fullscreen button found")
                test_results.append(("Exit Fullscreen Button", "PASS"))
            else:
                print("  ? Exit fullscreen button not found")
                test_results.append(("Exit Fullscreen Button", "WARN"))

            # Step 7: Exit fullscreen and verify bell button returns to header
            print("\nStep 7: Exit fullscreen mode")
            if exit_btn.count() > 0:
                exit_btn.click()
                time.sleep(1)
                screenshots.append(take_screenshot(page, "07_exited_fullscreen.png"))

                # Verify page-header visible again
                page_header_visible = page.locator(".page-header:not(.d-none)")
                if page_header_visible.count() > 0:
                    print("  ✓ Page-header visible after exiting fullscreen")
                    test_results.append(("Page-header Restored", "PASS"))

                    # Bell button should be back in header
                    bell_btn_back = page.locator(".page-header button:has(.bi-bell), .page-header button:has(.bi-bell-slash)")
                    if bell_btn_back.count() > 0:
                        print("  ✓ Bell button back in page-header")
                        test_results.append(("Bell Button Restored", "PASS"))
                    else:
                        print("  ? Bell button not found in page-header")
                        test_results.append(("Bell Button Restored", "WARN"))
                else:
                    print("  ? Page-header still hidden")
                    test_results.append(("Page-header Restored", "WARN"))

            # Print summary
            print("\n========================================")
            print("Test Summary")
            print("========================================")

            passed = sum(1 for r in test_results if r[1] == "PASS")
            failed = sum(1 for r in test_results if r[1] == "FAIL")
            warned = sum(1 for r in test_results if r[1] in ["WARN", "INFO"])

            for result in test_results:
                icon = "✓" if result[1] == "PASS" else "✗" if result[1] == "FAIL" else "?"
                detail = f" - {result[2]}" if len(result) > 2 else ""
                print(f"  {icon} {result[0]}: {result[1]}{detail}")

            print(f"\nTotal: {passed} passed, {failed} failed, {warned} warnings/info")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")
            print("========================================")

            return failed == 0

        except Exception as e:
            print(f"\nError during test: {e}")
            import traceback
            traceback.print_exc()
            screenshots.append(take_screenshot(page, "error.png"))
            return False

        finally:
            browser.close()

if __name__ == "__main__":
    success = test_issue73()
    sys.exit(0 if success else 1)