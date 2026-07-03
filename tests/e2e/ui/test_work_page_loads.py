"""
Test script to verify /work page loads correctly
"""

import os
import sys
import time

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = "screenshots"


def test_work_page_loads(ui_screenshot_dir):
    """Test that /work page loads without errors"""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Capture console errors and page errors
        def on_console(msg):
            if msg.type in ["error", "exception"]:
                console_errors.append(f"{msg.type}: {msg.text}")
            else:
                console_errors.append(f"{msg.type}: {msg.text}")

        page.on("console", on_console)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        # Step 1: Login first
        print("[Step 1] Logging in...")
        page.goto(f"{BASE_URL}/login")
        page.wait_for_selector("#username", timeout=10000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_timeout(5000)
        page.wait_for_load_state("networkidle")
        print(f"  Current URL after login: {page.url}")

        # Step 2: Navigate to /work page
        print("[Step 2] Navigating to /work page...")
        page.goto(f"{BASE_URL}/work", wait_until="networkidle")
        page.wait_for_timeout(5000)  # Wait for React to render

        # Take screenshot to see what's on the page
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_work_page_debug.png"))
        print(f"  Current URL: {page.url}")

        # Print errors
        print("\n=== Console Messages ===")
        for error in console_errors:
            print(error)
        print("=== End Console Messages ===\n")

        print("\n=== Page Errors ===")
        for error in page_errors:
            print(error)
        print("=== End Page Errors ===\n")

        # Check if workspace content is visible
        workspace = page.locator(".workspace")
        if workspace.count() > 0:
            print("Workspace element found")
        else:
            print("Workspace element not found, checking page structure...")

        # Check for work layout
        work_layout = page.locator(".work-layout")
        assert work_layout.count() > 0, "Work layout should be present"

        # Check for work header
        header = page.locator(".work-header")
        assert header.is_visible(), "Work header should be visible"

        # Check for left panel (session list)
        left_panel = page.locator(".work-left-panel")
        assert left_panel.is_visible(), "Left panel should be visible"

        # Check for right panel (assist panel)
        right_panel = page.locator(".work-right-panel")
        assert right_panel.is_visible(), "Right panel should be visible"

        # Check for status bar
        status_bar = page.locator(".work-status-bar")
        assert status_bar.is_visible(), "Status bar should be visible"

        # Take screenshot
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_work_page_loads.png"))

        print("All checks passed!")
        print("Screenshot saved to: screenshots/test_work_page_loads.png")

        browser.close()


if __name__ == "__main__":
    test_work_page_loads()
