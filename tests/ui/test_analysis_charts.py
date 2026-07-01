#!/usr/bin/env python3
"""
Analysis Charts UI Test

Test that Analysis page charts display data correctly:
- Usage Heatmap
- Token Trend
- Peak Usage Periods
- Top 10 Active Users
- Tool Comparison
- Token Distribution
"""

import os
import sys

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import time

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take a screenshot and save it."""
    path = os.path.join(SCREENSHOT_DIR, f"analysis_charts_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_analysis_charts():
    """Test that Analysis page charts display data."""
    screenshots = []
    results = {
        "usage_heatmap": False,
        "token_trend": False,
        "peak_usage": False,
        "active_users": False,
        "tool_comparison": False,
        "token_distribution": False,
    }

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "01_login"))

            # Step 2: Login
            print("[Step 2] Login")
            page.wait_for_selector("#username", timeout=10000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            # Wait for SPA navigation - the URL changes from /login to /manage/dashboard
            page.wait_for_timeout(5000)
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "02_after_login"))

            # Step 3: Navigate to Analysis page (Trend Analysis)
            print("[Step 3] Navigate to Analysis page")
            # Admin users are redirected to /manage/dashboard after login.
            # The Analysis charts are now at /manage/analysis/trend (TrendAnalysis component).
            page.goto(f"{BASE_URL}/manage/analysis/trend")
            page.wait_for_load_state("networkidle")
            time.sleep(3)
            screenshots.append(take_screenshot(page, "03_analysis"))

            # Step 4: Check Usage Heatmap
            print("[Step 4] Check Usage Heatmap")
            heatmap_heading = page.locator(
                'h5:has-text("Usage Heatmap"), .card-title:has-text("Usage Heatmap"), h5:has-text("使用热力图")'
            )
            # Wait for the page to fully render with data
            page.wait_for_timeout(3000)
            if heatmap_heading.count() > 0:
                print("  ✓ Usage Heatmap heading found")
                # Check for heatmap cells
                heatmap_cells = page.locator(".heatmap-cell, .usage-heatmap")
                if heatmap_cells.count() > 0:
                    print(f"  ✓ Found {heatmap_cells.count()} heatmap cell(s)")
                    results["usage_heatmap"] = True
                else:
                    print("  ✗ No heatmap cells found")
            else:
                print("  ✗ Usage Heatmap heading not found")

            # Step 5: Check Token Trend
            print("[Step 5] Check Token Trend")
            token_trend_heading = page.locator(
                'h5:has-text("Token Trend"), .card-title:has-text("Token Trend"), h5:has-text("Token 趋势")'
            )
            if token_trend_heading.count() > 0:
                print("  ✓ Token Trend heading found")
                # Check for chart or data
                token_trend_card = token_trend_heading.first.locator("xpath=..")
                no_data = token_trend_card.locator('text="No data available", text="暂无数据"')
                if no_data.count() == 0:
                    print("  ✓ Token Trend has data")
                    results["token_trend"] = True
                else:
                    print("  ✗ Token Trend shows 'No data available'")
            else:
                print("  ✗ Token Trend heading not found")

            # Step 6: Check Peak Usage Periods
            print("[Step 6] Check Peak Usage Periods")
            peak_heading = page.locator(
                'h5:has-text("Peak Usage"), .card-title:has-text("Peak Usage"), h5:has-text("峰值")'
            )
            if peak_heading.count() > 0:
                print("  ✓ Peak Usage Periods heading found")
                # Check for table rows - use ancestor to get the card
                peak_card = peak_heading.first.locator(
                    'xpath=ancestor::div[contains(@class, "card")]'
                )
                table_rows = peak_card.locator("tbody tr")
                if table_rows.count() > 0:
                    print(f"  ✓ Found {table_rows.count()} peak usage row(s)")
                    results["peak_usage"] = True
                else:
                    no_data = peak_card.locator('text="No data available", text="暂无数据"')
                    if no_data.count() > 0:
                        print("  ✗ Peak Usage shows 'No data available'")
                    else:
                        print("  ⚠ No table rows found but no 'No data' message")
            else:
                print("  ✗ Peak Usage Periods heading not found")

            # Step 7: Check Top 10 Active Users
            print("[Step 7] Check Top 10 Active Users")
            users_heading = page.locator(
                'h5:has-text("Active Users"), .card-title:has-text("Active Users"), h5:has-text("活跃用户")'
            )
            if users_heading.count() > 0:
                print("  ✓ Active Users heading found")
                # Check for table rows - use ancestor to get the card
                users_card = users_heading.first.locator(
                    'xpath=ancestor::div[contains(@class, "card")]'
                )
                table_rows = users_card.locator("tbody tr")
                if table_rows.count() > 0:
                    print(f"  ✓ Found {table_rows.count()} user row(s)")
                    results["active_users"] = True
                else:
                    no_data = users_card.locator('text="No data available", text="暂无数据"')
                    if no_data.count() > 0:
                        print("  ✗ Active Users shows 'No data available'")
                    else:
                        print("  ⚠ No table rows found but no 'No data' message")
            else:
                print("  ✗ Active Users heading not found")

            # Step 8: Check Tool Comparison
            print("[Step 8] Check Tool Comparison")
            tool_comp_heading = page.locator(
                'h5:has-text("Tool Comparison"), .card-title:has-text("Tool Comparison"), h5:has-text("工具对比")'
            )
            if tool_comp_heading.count() > 0:
                print("  ✓ Tool Comparison heading found")
                tool_comp_card = tool_comp_heading.first.locator("xpath=..")
                no_data = tool_comp_card.locator('text="No data available", text="暂无数据"')
                if no_data.count() == 0:
                    print("  ✓ Tool Comparison has data")
                    results["tool_comparison"] = True
                else:
                    print("  ✗ Tool Comparison shows 'No data available'")
            else:
                print("  ✗ Tool Comparison heading not found")

            # Step 9: Check Token Distribution
            print("[Step 9] Check Token Distribution")
            token_dist_heading = page.locator(
                'h5:has-text("Token Distribution"), .card-title:has-text("Token Distribution"), h5:has-text("Token 分布")'
            )
            if token_dist_heading.count() > 0:
                print("  ✓ Token Distribution heading found")
                token_dist_card = token_dist_heading.first.locator("xpath=..")
                no_data = token_dist_card.locator('text="No data available", text="暂无数据"')
                if no_data.count() == 0:
                    print("  ✓ Token Distribution has data")
                    results["token_distribution"] = True
                else:
                    print("  ✗ Token Distribution shows 'No data available'")
            else:
                print("  ✗ Token Distribution heading not found")

            # Step 10: Final screenshot
            print("[Step 10] Final screenshot")
            screenshots.append(take_screenshot(page, "04_final"))

            # Summary
            print("\n" + "=" * 60)
            print("Analysis Charts Test Summary")
            print("=" * 60)

            passed = sum(1 for v in results.values() if v)
            total = len(results)

            print(f"\nResults: {passed}/{total} checks passed")
            for name, passed_check in results.items():
                status = "✓ PASS" if passed_check else "✗ FAIL"
                print(f"  {status}: {name}")

            print(f"\nScreenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

            if passed == total:
                print("\n✓ All tests PASSED!")
            else:
                print(f"\n⚠ {total - passed} test(s) FAILED")

        except Exception as e:
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error"))
            raise
        finally:
            browser.close()

    return screenshots, results


if __name__ == "__main__":
    test_analysis_charts()
