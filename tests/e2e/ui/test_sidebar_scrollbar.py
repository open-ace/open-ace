"""
Test script for issue #77: Sidebar menu no scrollbar.

Issue: Sidebar menu shows scrollbar when content overflows.

Fix: Add CSS to hide scrollbar while keeping scroll functionality.
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
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(77)]


# Configuration
BASE_URL = "http://localhost:19888"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "77")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


async def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    await page.screenshot(path=path)
    return path


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_sidebar_scrollbar():
    """Test #77: Sidebar menu has no visible scrollbar."""
    _skip_if_no_server()
    print("\n" + "=" * 50)
    print("Test #77: Sidebar menu no scrollbar")
    print("=" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Login first
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_load_state("networkidle")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click("#login-btn")
            await page.wait_for_url(f"{BASE_URL}/", timeout=10000)
            await page.wait_for_load_state("networkidle")

            # Take screenshot of sidebar
            screenshot_path = await take_screenshot(page, "01_sidebar.png")
            print(f"  Screenshot: {screenshot_path}")

            # Check sidebar-nav has scrollbar-width: none
            sidebar_nav = await page.locator("#sidebar-nav")
            scrollbar_width = sidebar_nav.evaluate(
                "el => window.getComputedStyle(el).scrollbarWidth"
            )
            print(f"  ✓ Sidebar scrollbar-width: {scrollbar_width}")
            assert (
                scrollbar_width == "none"
            ), f"sidebar-nav scrollbar-width should be 'none', got {scrollbar_width!r}"

            # Note: scrollbar-width: none is the CSS property
            print("  ✓ Test #77 PASSED (CSS property set)")
            return True

        except PlaywrightError as e:
            print(f"  ✗ Test #77 FAILED: {e}")
            await take_screenshot(page, "error_77.png")
            return False
        finally:
            await browser.close()
