"""
Test issue 91: My Usage Report page should have Token Usage and Request Chart instead of Usage By Tool.

#2491 R3a realignment: the baselined failure clicked ``#login-btn`` and
expected ``#report-section``, ``#token-usage-title``,
``#reportTokenUsageChart``, ``#request-chart-title`` and ``#lang-select``.
The React report page is served at the ``/report`` route
(``frontend/src/App.tsx`` — ``<Route path="/report" element={<LegacyAppContent />} />``
rendering ``Report``), with an ``h2`` "My Usage Report" and two chart cards
titled "Token Trend" (``tokenTrend``) and "Token Distribution"
(``tokenDistribution``) — ``frontend/src/components/features/Report.tsx``
lines 85-195 (``Card title={t('tokenTrend')}`` / ``Card title={t('tokenDistribution')}``).
Chart.js canvases only mount when the usage query returns data, so the
data-less lane asserts the card titles (the two-chart contract of issue 91)
plus the removal of the old "Usage By Tool" chart, and exercises the header
globe dropdown (``frontend/src/components/layout/Header.tsx`` lines 121-164)
for language switching. The invalid default credentials (testuser91) are
replaced by the lane's TEST_USERNAME/TEST_PASSWORD pair.

This test verifies that:
1. The report page displays two separate charts: Token Trend and Token Distribution
2. The old "Usage By Tool" chart is gone
3. Language switching updates chart titles correctly
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

import time

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(91)]


# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
    "91",
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_report_charts():
    """Test that My Usage Report page has Token Trend and Token Distribution charts."""
    _skip_if_no_server()
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        test_results = []

        try:
            # Step 1: Navigate to login page
            print("Step 1: Navigate to login page...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_load_state("networkidle")
            time.sleep(1)

            # Step 2: Login (submit button inside .login-form)
            print("Step 2: Login...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click(".login-form button[type='submit']")
            page.wait_for_url("**/manage/**", timeout=15000)
            time.sleep(1)

            test_results.append(("Login", "PASS", "Successfully logged in"))

            # Step 3: Navigate to My Usage Report (/report route)
            print("Step 3: Navigate to My Usage Report...")
            page.goto(f"{BASE_URL}report")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".report", timeout=15000)
            time.sleep(2)

            heading = page.locator(".report h2")
            expect(heading).to_be_visible()
            assert "My Usage Report" in heading.inner_text()
            test_results.append(("Navigate to Report", "PASS", f"heading: {heading.inner_text()}"))

            # Take screenshot of report page
            screenshot_path = os.path.join(SCREENSHOT_DIR, "report_page_initial.png")
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

            # Step 4: Verify the two chart cards exist (Token Trend + Token Distribution)
            print("Step 4: Verify chart cards...")
            card_titles = page.locator(".report .card-title")
            titles = [card_titles.nth(i).inner_text() for i in range(card_titles.count())]

            if "Token Trend" in titles:
                test_results.append(("Token Trend Chart", "PASS", f"cards: {titles}"))
            else:
                test_results.append(("Token Trend Chart", "FAIL", f"cards: {titles}"))

            if "Token Distribution" in titles:
                test_results.append(("Token Distribution Chart", "PASS", f"cards: {titles}"))
            else:
                test_results.append(("Token Distribution Chart", "FAIL", f"cards: {titles}"))

            # Step 5: Verify old "Usage by Tool" chart no longer exists
            print("Step 5: Verify old chart no longer exists...")
            body_text = page.locator("body").inner_text()
            if "Usage By Tool" not in body_text and "Usage by Tool" not in body_text:
                test_results.append(
                    ("Old Chart Removed", "PASS", "Old 'Usage By Tool' chart removed")
                )
            else:
                test_results.append(
                    ("Old Chart Removed", "FAIL", "Old 'Usage By Tool' chart still exists")
                )

            # Step 6: Test language switching (English -> Chinese) via header globe
            print("Step 6: Test language switching...")
            globe = page.locator("header button:has(i.bi-globe)")
            expect(globe).to_be_visible()
            globe.click()
            time.sleep(0.5)
            page.locator(".dropdown-menu.show .dropdown-item", has_text="Chinese").click()
            time.sleep(1)
            page.wait_for_load_state("networkidle")

            # Verify Chinese titles
            card_titles_zh = page.locator(".report .card-title")
            titles_zh = [card_titles_zh.nth(i).inner_text() for i in range(card_titles_zh.count())]

            # Take screenshot with Chinese language
            screenshot_path_zh = os.path.join(SCREENSHOT_DIR, "report_page_chinese.png")
            page.screenshot(path=screenshot_path_zh)
            print(f"Screenshot saved: {screenshot_path_zh}")

            if "Token 趋势" in titles_zh:
                test_results.append(("Chinese Token Title", "PASS", f"cards: {titles_zh}"))
            else:
                test_results.append(
                    (
                        "Chinese Token Title",
                        "FAIL",
                        f"Expected 'Token 趋势' among cards, got '{titles_zh}'",
                    )
                )

            if "Token 分布" in titles_zh:
                test_results.append(("Chinese Distribution Title", "PASS", f"cards: {titles_zh}"))
            else:
                test_results.append(
                    (
                        "Chinese Distribution Title",
                        "FAIL",
                        f"Expected 'Token 分布' among cards, got '{titles_zh}'",
                    )
                )

            # Step 7: Switch back to English. The option labels are localized
            # ("English" becomes "英语" while the UI is Chinese), but the items
            # keep their order: English is always the first entry.
            print("Step 7: Switch back to English...")
            globe.click()
            time.sleep(0.5)
            page.locator(".dropdown-menu.show .dropdown-item").first.click()
            time.sleep(1)
            page.wait_for_load_state("networkidle")

            card_titles_en = page.locator(".report .card-title")
            titles_en = [card_titles_en.nth(i).inner_text() for i in range(card_titles_en.count())]

            if "Token Trend" in titles_en:
                test_results.append(("English Token Title", "PASS", f"cards: {titles_en}"))
            else:
                test_results.append(
                    ("English Token Title", "FAIL", f"Expected 'Token Trend', got '{titles_en}'")
                )

            # Take final screenshot
            screenshot_path_final = os.path.join(SCREENSHOT_DIR, "report_page_final.png")
            page.screenshot(path=screenshot_path_final)
            print(f"Screenshot saved: {screenshot_path_final}")

        except (AssertionError, PlaywrightError) as e:
            test_results.append(("Error", "FAIL", str(e)))
            # Take error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error_screenshot.png")
            page.screenshot(path=error_screenshot)
            print(f"Error screenshot saved: {error_screenshot}")

        finally:
            browser.close()

        # Print test report
        print("\n" + "=" * 60)
        print("UI Test Report - Issue 91")
        print("=" * 60)
        print(f"Test Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Tests: {len(test_results)}")

        passed = sum(1 for r in test_results if r[1] == "PASS")
        failed = sum(1 for r in test_results if r[1] == "FAIL")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print("-" * 60)

        for name, status, message in test_results:
            status_icon = "✓" if status == "PASS" else "✗"
            print(f"  [{status_icon}] {name}: {message}")

        print("-" * 60)
        print(f"Screenshots saved in: {SCREENSHOT_DIR}")
        print("=" * 60)

        assert (
            failed == 0
        ), f"{failed} report-chart check(s) failed: {[r for r in test_results if r[1] == 'FAIL']}"
        return failed == 0
