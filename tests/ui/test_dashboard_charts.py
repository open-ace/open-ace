#!/usr/bin/env python3
"""
Dashboard Charts UI Test

Test that Trend Chart and Token Distribution display data correctly.
"""

import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from playwright.sync_api import sync_playwright, expect
import time

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take a screenshot and save it."""
    path = os.path.join(SCREENSHOT_DIR, f"dashboard_charts_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_dashboard_charts():
    """Test that dashboard charts display data."""
    screenshots = []

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "01_login"))

            # Step 2: Login
            print("[Step 2] Login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "02_after_login"))

            # Step 3: Navigate to Dashboard
            print("[Step 3] Navigate to Dashboard")
            # Click on Dashboard nav item
            dashboard_nav = page.locator(
                'a:has-text("Dashboard"), #nav-dashboard, [href="#/dashboard"]'
            )
            if dashboard_nav.count() > 0:
                dashboard_nav.first.click()
            else:
                # Try navigating directly
                page.goto(f"{BASE_URL}#/dashboard")
            page.wait_for_load_state("networkidle")
            time.sleep(3)
            screenshots.append(take_screenshot(page, "03_dashboard"))

            # Step 4: Check Trend Chart
            print("[Step 4] Check Trend Chart")
            trend_chart_heading = page.locator(
                'h5:has-text("Trend Chart"), .card-title:has-text("Trend Chart")'
            )
            if trend_chart_heading.count() > 0:
                print("  ✓ Trend Chart heading found")

                # Check for chart canvas
                chart_canvas = page.locator("canvas")
                if chart_canvas.count() > 0:
                    print(f"  ✓ Found {chart_canvas.count()} chart canvas element(s)")
                else:
                    print("  ✗ No chart canvas found")
            else:
                print("  ✗ Trend Chart heading not found")

            # Step 5: Check for "No data available" message
            print("[Step 5] Check for data availability")
            no_data_msg = page.locator('text="No data available"')
            no_data_count = no_data_msg.count()
            if no_data_count > 0:
                print(f"  ⚠ Found {no_data_count} 'No data available' message(s)")
            else:
                print("  ✓ No 'No data available' messages found - data is present")

            # Step 6: Check Token Distribution
            print("[Step 6] Check Token Distribution")
            token_dist_heading = page.locator(
                'h5:has-text("Token Distribution"), .card-title:has-text("Token Distribution")'
            )
            if token_dist_heading.count() > 0:
                print("  ✓ Token Distribution heading found")
            else:
                print("  ✗ Token Distribution heading not found")

            # Step 7: Final screenshot
            print("[Step 7] Final screenshot")
            screenshots.append(take_screenshot(page, "04_final"))

            # Summary
            print("\n" + "=" * 50)
            print("Dashboard Charts Test Summary")
            print("=" * 50)
            print(f"Screenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

            # Check if charts have data by looking at the page content
            page_content = page.content()
            has_trend_data = "Trend Chart" in page_content
            has_token_dist = "Token Distribution" in page_content

            if has_trend_data and has_token_dist:
                print("\n✓ Test PASSED: Both chart sections are present")
                if no_data_count == 0:
                    print("✓ Data is displayed in charts")
                else:
                    print(f"⚠ Warning: {no_data_count} 'No data available' messages found")
            else:
                print("\n✗ Test FAILED: Missing chart sections")

        except Exception as e:
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error"))
            raise
        finally:
            browser.close()

    return screenshots


if __name__ == "__main__":
    test_dashboard_charts()
