"""
Test issue 92: Move Logout button above language selector.

This test verifies that:
1. The Logout button is positioned above the language selector
2. Both elements are visible in the sidebar footer
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

import time

from playwright.sync_api import expect, sync_playwright

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "testuser91")
PASSWORD = os.environ.get("PASSWORD", "test123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
    "92",
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_logout_position():
    """Test that Logout button is above language selector."""
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        test_results = []

        try:
            # Step 1: Navigate to login page
            print("Step 1: Navigate to login page...")
            page.goto(BASE_URL + "login")
            page.wait_for_load_state("networkidle")
            time.sleep(1)

            # Step 2: Login
            print("Step 2: Login...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("#login-btn")

            # Wait for sidebar to appear (indicates successful login)
            page.wait_for_selector("#sidebar", timeout=15000)
            time.sleep(2)

            expect(page.locator("#sidebar")).to_be_visible()
            test_results.append(("Login", "PASS", "Successfully logged in"))

            # Take screenshot of sidebar
            screenshot_path = os.path.join(SCREENSHOT_DIR, "sidebar_layout.png")
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

            # Step 3: Verify Logout button exists and is visible
            print("Step 3: Verify Logout button...")
            logout_btn = page.locator("#nav-logout")
            expect(logout_btn).to_be_visible()
            test_results.append(("Logout Button Visible", "PASS", "Logout button is visible"))

            # Step 4: Verify language selector exists and is visible
            print("Step 4: Verify language selector...")
            lang_select = page.locator("#lang-select")
            expect(lang_select).to_be_visible()
            test_results.append(
                ("Language Selector Visible", "PASS", "Language selector is visible")
            )

            # Step 5: Verify Logout button is above language selector
            print("Step 5: Verify Logout button position...")
            logout_box = logout_btn.bounding_box()
            lang_box = lang_select.bounding_box()

            if logout_box and lang_box:
                # Logout button should have smaller Y coordinate (higher on page)
                if logout_box["y"] < lang_box["y"]:
                    test_results.append(
                        (
                            "Logout Above Language",
                            "PASS",
                            f"Logout Y: {logout_box['y']:.0f}, Lang Y: {lang_box['y']:.0f}",
                        )
                    )
                else:
                    test_results.append(
                        (
                            "Logout Above Language",
                            "FAIL",
                            f"Logout Y: {logout_box['y']:.0f} should be < Lang Y: {lang_box['y']:.0f}",
                        )
                    )
            else:
                test_results.append(
                    ("Logout Above Language", "FAIL", "Could not get bounding boxes")
                )

            # Step 6: Verify the order in DOM
            print("Step 6: Verify DOM order...")
            # Get the parent container
            sidebar_footer = page.locator(".sidebar-footer")
            footer_html = sidebar_footer.inner_html()

            # Check that nav-logout appears before lang-select in the HTML
            logout_pos = footer_html.find("nav-logout")
            lang_pos = footer_html.find("lang-select")

            if logout_pos != -1 and lang_pos != -1 and logout_pos < lang_pos:
                test_results.append(
                    (
                        "DOM Order Correct",
                        "PASS",
                        f"Logout at position {logout_pos}, Lang at position {lang_pos}",
                    )
                )
            else:
                test_results.append(
                    ("DOM Order Correct", "FAIL", f"Logout at {logout_pos}, Lang at {lang_pos}")
                )

        except Exception as e:
            test_results.append(("Error", "FAIL", str(e)))
            # Take error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error_screenshot.png")
            page.screenshot(path=error_screenshot)
            print(f"Error screenshot saved: {error_screenshot}")

        finally:
            browser.close()

        # Print test report
        print("\n" + "=" * 60)
        print("UI Test Report - Issue 92")
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

        return failed == 0


if __name__ == "__main__":
    success = test_logout_position()
    sys.exit(0 if success else 1)
