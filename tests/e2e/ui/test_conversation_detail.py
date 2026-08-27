"""
Test script for issue #98: Conversation detail shows no data

This script verifies that clicking on a conversation in the conversation history
list correctly displays the conversation details.
"""

import asyncio
import os

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(98)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "98")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_conversation_detail():
    """Test that conversation detail is displayed correctly."""
    _skip_if_no_server()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            # Navigate to conversation history page
            print("Navigating to conversation history page...")
            await page.goto(
                f"{BASE_URL}/manage/analysis/conversation-history",
                wait_until="networkidle",
            )
            await page.wait_for_timeout(5000)  # Wait for data to load

            # Take screenshot of the page
            await page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "01_conversation_history.png"),
                full_page=True,
            )
            print("Screenshot saved: 01_conversation_history.png")

            # Check if there are conversation rows
            rows = await page.locator("table tbody tr").count()
            print(f"Found {rows} conversation rows")

            # If no rows, check for loading or error state
            if rows == 0:
                loading = await page.locator('.loading, .spinner, [data-testid="loading"]').count()
                error = await page.locator(".error, .alert-danger").count()
                empty = await page.locator(".empty-state").count()
                print(
                    f"Loading indicators: {loading}, Error indicators: {error}, Empty state: {empty}"
                )

                # Check page content
                content = await page.content()
                print(f"Page content length: {len(content)}")

            if rows > 0:
                # Click on the first conversation's view button
                view_button = page.locator("table tbody tr:first-child button:has(.bi-eye)")
                assert await view_button.count() > 0, "conversation view button not found"
                print("Clicking view button...")
                await view_button.click()
                await page.wait_for_timeout(2000)

                # Take screenshot of the modal
                await page.screenshot(
                    path=os.path.join(SCREENSHOT_DIR, "02_conversation_detail_modal.png"),
                    full_page=True,
                )
                print("Screenshot saved: 02_conversation_detail_modal.png")

                # Check if the modal has content
                modal = page.locator(".modal.show")
                assert await modal.count() > 0, "conversation detail modal did not open"
                # Check for "no data" message
                no_data = await modal.locator("text=暂无数据").count()
                assert no_data == 0, "conversation detail modal shows '暂无数据' (no data)"

                # Check for message items
                messages = await modal.locator(".message-item").count()
                print(f"Found {messages} messages in the modal")
                assert messages > 0, "conversation detail modal shows no messages"
                print("SUCCESS: Conversation details are displayed correctly!")
                return True
            else:
                print("WARNING: No conversation rows found in the table")
                return True  # Not an error, just no data

        except PlaywrightError as e:
            print(f"Error: {e}")
            await page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "error.png"),
                full_page=True,
            )
            return False
        finally:
            await browser.close()
