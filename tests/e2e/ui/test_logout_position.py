"""
Test issue 92: Move Logout button above language selector.

#2491 R3a realignment: the baselined failure clicked ``#login-btn`` and waited
for ``#sidebar``. The React app has neither: the login submit control is
``button[type="submit"]`` inside ``form.login-form``
(``frontend/src/components/features/Login.tsx`` lines 289-320) and the manage
sidebar is ``nav.manage-sidebar``
(``frontend/src/components/layout/ManageLayout.tsx`` line 361). The sidebar
footer (``#nav-logout`` / ``#lang-select`` / ``.sidebar-footer``) no longer
exists: both actions moved into the header — the language switcher is the
globe dropdown (``frontend/src/components/layout/Header.tsx`` lines 121-164,
icon ``bi-globe``) and Logout is the last item of the user dropdown
(Header.tsx lines 191-210, items: email text, Settings, Logout; clicking it
redirects to /login via ``useAuth.logout``). The relative-position intent is
re-targeted to that layout: the language control precedes the user menu in
the header DOM, Logout is reachable inside the user menu, and clicking it
returns to the login page.

This test verifies that:
1. The Logout action is available in the header user menu (last item)
2. The language selector is available in the same header (globe dropdown)
3. The language dropdown toggle appears before the user-menu toggle
4. Clicking Logout navigates back to the login page
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

pytestmark = [pytest.mark.regression, pytest.mark.issue(92)]


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
    "92",
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_logout_position():
    """Logout and language controls live in the header; Logout ends the session."""
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
            # Login lands on "/" (work shell); the manage header/sidebar this
            # test verifies lives under /manage — navigate there explicitly.
            page.wait_for_url(f"{BASE_URL}", timeout=15000)
            page.goto(f"{BASE_URL}manage/dashboard")
            time.sleep(2)

            expect(page.locator("nav.manage-sidebar")).to_be_visible()
            test_results.append(("Login", "PASS", "Successfully logged in"))

            # Take screenshot of the manage layout
            screenshot_path = os.path.join(SCREENSHOT_DIR, "sidebar_layout.png")
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

            # Step 3: The language selector is the header globe dropdown
            print("Step 3: Verify language selector...")
            globe = page.locator("header button:has(i.bi-globe)")
            expect(globe).to_be_visible()
            globe.click()
            time.sleep(0.5)
            lang_items = page.locator(".dropdown-menu.show .dropdown-item")
            lang_texts = [lang_items.nth(i).inner_text() for i in range(lang_items.count())]
            if "Chinese" in lang_texts and "English" in lang_texts:
                test_results.append(("Language Selector Visible", "PASS", f"options: {lang_texts}"))
            else:
                test_results.append(("Language Selector Visible", "FAIL", f"options: {lang_texts}"))
            page.keyboard.press("Escape")
            time.sleep(0.5)

            # Step 4: The user menu contains the Logout action (last item)
            print("Step 4: Verify Logout in user menu...")
            user_toggle = page.locator("header .dropdown-toggle.d-flex")
            expect(user_toggle).to_be_visible()
            user_toggle.click()
            time.sleep(0.5)
            menu_items = page.locator(".dropdown-menu.show .dropdown-item")
            item_texts = [menu_items.nth(i).inner_text() for i in range(menu_items.count())]
            if item_texts and item_texts[-1] == "Logout":
                test_results.append(("Logout Button Visible", "PASS", f"menu items: {item_texts}"))
            else:
                test_results.append(
                    ("Logout Button Visible", "FAIL", f"Logout should be last, got: {item_texts}")
                )

            # Step 5: Language control precedes the user menu in the header
            print("Step 5: Verify control order in header...")
            lang_box = globe.bounding_box()
            user_box = user_toggle.bounding_box()
            if lang_box and user_box and lang_box["x"] < user_box["x"]:
                test_results.append(
                    (
                        "Language Before User Menu",
                        "PASS",
                        f"lang x: {lang_box['x']:.0f} < user x: {user_box['x']:.0f}",
                    )
                )
            else:
                test_results.append(
                    (
                        "Language Before User Menu",
                        "FAIL",
                        f"lang x: {lang_box and lang_box['x']}, user x: {user_box and user_box['x']}",
                    )
                )

            # Step 6: Clicking Logout returns to the login page
            print("Step 6: Click Logout...")
            logout_item = page.locator(".dropdown-menu.show .dropdown-item", has_text="Logout")
            logout_item.click()
            page.wait_for_url("**/login**", timeout=15000)
            expect(page.locator(".login-form")).to_be_visible()
            test_results.append(("Logout Navigation", "PASS", f"redirected to {page.url}"))

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

        assert (
            failed == 0
        ), f"{failed} issue-92 check(s) failed: {[r for r in test_results if r[1] == 'FAIL']}"
        return failed == 0
