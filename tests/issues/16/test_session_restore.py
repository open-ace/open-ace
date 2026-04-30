#!/usr/bin/env python3
"""
Test session restore functionality (Issue #16)

Tests:
1. Navigate to Sessions page
2. Check session list is visible
3. Click 'Restore to Workspace' button
4. Verify workspace opens with restored session
"""

import os
import sys

from playwright.sync_api import expect, sync_playwright

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1920, "height": 1080}
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def test_session_restore():
    """Test session restore functionality"""

    print("=" * 60)
    print("Session Restore Functionality Test (Issue #16)")
    print("=" * 60)

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        test_results = []

        # Step 1: Navigate to login page
        print("\n[Step 1] Navigate to login page...")
        page.goto(f"{BASE_URL}/", timeout=30000)

        # Wait for React to load and login form to appear
        expect(page.locator("#username")).to_be_visible(timeout=10000)
        test_results.append(("Navigate to login page", True))
        print("  ✓ Login page loaded")

        # Step 2: Login
        print("\n[Step 2] Login...")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')

        # Wait for redirect after login (manage dashboard)
        page.wait_for_url("**/manage/**", timeout=10000)
        import time

        time.sleep(1)  # Wait for page to stabilize
        test_results.append(("Login successful", True))
        print("  ✓ Login successful")

        # Step 3: Navigate to Sessions page (work mode)
        print("\n[Step 3] Navigate to Sessions page (work mode)...")
        # Navigate directly to work/sessions
        page.goto(f"{BASE_URL}/work/sessions", timeout=30000)
        page.wait_for_selector(".sessions", timeout=10000)
        time.sleep(2)  # Wait for data to load
        test_results.append(("Navigate to Sessions page", True))
        print("  ✓ Sessions page loaded")

        # Step 4: Check session list is visible
        print("\n[Step 4] Check session list...")
        session_items = page.locator(".session-item")
        session_count = session_items.count()
        print(f"  Found {session_count} sessions")

        if session_count > 0:
            test_results.append(("Session list visible", True))
            print("  ✓ Session list visible")
        else:
            test_results.append(("Session list visible", False))
            print("  ✗ No sessions found")
            browser.close()
            return False

        # Step 5: Find and click 'Restore to Workspace' button
        print("\n[Step 5] Click 'Restore to Workspace' button...")

        # Listen for console messages
        page.on("console", lambda msg: print(f"  Console {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"  Page Error: {err}"))

        # Find the first restore button (blue button with bi-box-arrow-in-right icon)
        restore_button = page.locator(
            ".session-item .btn-outline-primary .bi-box-arrow-in-right"
        ).first

        if restore_button.count() > 0:
            restore_button.click()
            test_results.append(("Click restore button", True))
            print("  ✓ Clicked restore button")

            # Wait a bit for the API call and navigation
            time.sleep(2)

            # Wait for navigation to workspace
            print("\n[Step 6] Wait for workspace to load...")
            try:
                # Get current URL before navigation
                url_before = page.url
                print(f"  URL before: {url_before}")

                # Wait for URL to change
                page.wait_for_function(f"window.location.href !== '{url_before}'", timeout=10000)

                current_url = page.url
                print(f"  URL after: {current_url}")
                test_results.append(("Navigate to workspace", True))
                print("  ✓ Navigated to workspace")

                # Wait for iframe to appear (may take longer to load)
                time.sleep(3)  # Wait for iframe to load

                # Check if we're on workspace page
                if "/work/workspace" in current_url or page.locator("iframe").count() > 0:
                    expect(page.locator("iframe")).to_be_visible(timeout=10000)
                    test_results.append(("Workspace iframe loaded", True))
                    print("  ✓ Workspace iframe loaded")
                else:
                    test_results.append(("Workspace iframe loaded", False))
                    print(f"  ✗ Not on workspace page: {current_url}")

                # Check URL contains sessionId parameter
                if "sessionId=" in current_url:
                    test_results.append(("URL contains sessionId", True))
                    print("  ✓ URL contains sessionId")
                else:
                    test_results.append(("URL contains sessionId", False))
                    print(f"  ✗ URL does not contain sessionId: {current_url}")

            except Exception as e:
                test_results.append(("Workspace loaded", False))
                print(f"  ✗ Workspace load failed: {e}")
                # Take screenshot for debugging
                debug_screenshot = os.path.join(
                    SCREENSHOT_DIR, "issues", "16", "debug_workspace.png"
                )
                page.screenshot(path=debug_screenshot)
                print(f"  Debug screenshot saved: {debug_screenshot}")
                # Print current URL for debugging
                print(f"  Current URL: {page.url}")
        else:
            test_results.append(("Click restore button", False))
            print("  ✗ No restore button found")

        # Take screenshot
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        screenshot_path = os.path.join(SCREENSHOT_DIR, "issues", "16", "test_session_restore.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        page.screenshot(path=screenshot_path)
        print(f"\nScreenshot saved: {screenshot_path}")

        # Close browser
        browser.close()

        # Print results
        print("\n" + "=" * 60)
        print("Test Results")
        print("=" * 60)

        passed = 0
        failed = 0

        for test_name, result in test_results:
            status = "✓" if result else "✗"
            print(f"  {status} {test_name}")
            if result:
                passed += 1
            else:
                failed += 1

        print("-" * 60)
        print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
        print("=" * 60)

        return failed == 0


if __name__ == "__main__":
    success = test_session_restore()
    sys.exit(0 if success else 1)
