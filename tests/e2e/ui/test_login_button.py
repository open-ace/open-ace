"""
Test script for issue #78: Login page Sign In button visible.

Issue: Sign In button is not visible on login page.

Fix: Fix CSS syntax error - missing ':root' selector prefix.

#2491 R3a realignment: the baselined failure waited for ``#login-btn``, an id
the React login page never renders. The current submit control is the
``Button`` component inside ``form.login-form``
(``frontend/src/components/features/Login.tsx`` lines 317-319) which renders
``<button type="submit" class="btn btn-primary w-100">`` with the localized
"Sign In" label (``frontend/src/components/common/Button.tsx``). The old
background-COLOR check is also stale: the app theme now styles ``.btn-primary``
with a gradient ``background`` (``background-image``), so the computed
``backgroundColor`` is deliberately transparent while the gradient paints the
button — the visibility assertion is re-targeted to the gradient.
"""

import os
import sys
from datetime import datetime

import pytest

# Get project root directory
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright, expect

pytestmark = [pytest.mark.regression, pytest.mark.issue(78)]


# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "78")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


async def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    await page.screenshot(path=path)
    return path


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_login_button():
    """Test #78: Login page Sign In button is visible."""
    _skip_if_no_server()
    print("\n" + "=" * 50)
    print("Test #78: Login page Sign In button visible")
    print("=" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Navigate to login page
            await page.goto(f"{BASE_URL}login")
            await page.wait_for_selector(".login-form button[type='submit']", timeout=15000)
            await page.wait_for_load_state("networkidle")

            # Take screenshot of login page
            screenshot_path = await take_screenshot(page, "01_login_page.png")
            print(f"  Screenshot: {screenshot_path}")

            # Check Sign In button exists and is visible
            login_btn = page.locator(".login-form button[type='submit']")
            await expect(login_btn).to_be_visible()

            # Check button has correct text
            btn_text = await login_btn.inner_text()
            assert "Sign In" in btn_text, f"Button text should contain 'Sign In', got: {btn_text}"
            print(f"  ✓ Sign In button is visible with text: '{btn_text}'")

            # Check the button is painted (theme styles .btn-primary with a
            # gradient background image; backgroundColor stays transparent).
            btn_style = await login_btn.evaluate(
                "el => ({ image: window.getComputedStyle(el).backgroundImage, "
                "color: window.getComputedStyle(el).backgroundColor })"
            )
            print(f"  ✓ Button background image: {btn_style['image']}")
            assert btn_style["image"] != "none", (
                "Button should be painted by the theme's .btn-primary gradient "
                f"(got background-image {btn_style['image']})"
            )

            print("  ✓ Test #78 PASSED")
            return True

        except PlaywrightError as e:
            print(f"  ✗ Test #78 FAILED: {e}")
            await take_screenshot(page, "error_78.png")
            raise
        finally:
            await browser.close()
