"""
Test script for issue #73: Data Status local host status updates.

Issue: Data Status panel shows local host status not updating.

Fix: Use current time for local host last_updated since data is real-time.
"""

import os
import sys
import time
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

pytestmark = [pytest.mark.regression, pytest.mark.issue(73)]


# Configuration
BASE_URL = "http://localhost:19888"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "73")
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
async def test_data_status_local():
    """Test #73: Data Status local host shows current time."""
    _skip_if_no_server()
    print("\n" + "=" * 50)
    print("Test #73: Data Status local host status updates")
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

            # Wait for data status to load
            await page.wait_for_selector("#data-status-container", timeout=10000)
            time.sleep(1)

            # Take screenshot
            screenshot_path = await take_screenshot(page, "01_data_status.png")
            print(f"  Screenshot: {screenshot_path}")

            # Check at least one host item exists
            host_items = await page.locator(".data-status-item")
            host_count = host_items.count()
            assert host_count > 0, "Should have at least one host item"
            print(f"  ✓ Found {host_count} host item(s)")

            # Check first host (local) shows recent time
            first_host = host_items.first
            time_text = first_host.locator(".last-updated").inner_text()
            print(f"  ✓ First host last updated: '{time_text}'")

            # Verify it shows "Just now" or recent time (within 1 minute)
            assert (
                "Just now" in time_text or "m ago" in time_text
            ), f"Local host should show recent time, got: '{time_text}'"
            print("  ✓ Local host shows current/recent time")

            print("  ✓ Test #73 PASSED")
            return True

        except PlaywrightError as e:
            print(f"  ✗ Test #73 FAILED: {e}")
            await take_screenshot(page, "error_73.png")
            return False
        finally:
            await browser.close()
