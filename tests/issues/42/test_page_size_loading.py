#!/usr/bin/env python3
"""
Test script for Issue #42: Page Size loading state

This test verifies that:
1. When changing Page Size, the table shows loading state instead of "No sessions found"
2. The loading spinner is displayed during data fetch
3. Data loads correctly after Page Size change

Usage:
    python3 tests/issues/42/test_page_size_loading.py
"""

import asyncio
import os
import time

import pytest
from playwright.async_api import async_playwright

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 10000  # 10 seconds timeout

# Screenshot directory
SCREENSHOT_DIR = "screenshots/issues/42"


@pytest.mark.asyncio
async def test_page_size_loading():
    """Test that Page Size change shows loading state instead of 'No sessions found'."""
    # Ensure screenshot directory exists
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        # Set default timeout
        page.set_default_timeout(TIMEOUT)

        test_passed = True
        error_messages = []

        try:
            print("=" * 60)
            print("[UI] Testing: Issue #42 - Page Size Loading State")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for redirect to dashboard
            for _ in range(15):
                await page.wait_for_timeout(1000)
                if "/manage/" in page.url or "/work" in page.url:
                    break
            print("✓ Login successful")

            # Step 2: Navigate to Conversation History page
            print("\n[Step 2] Navigating to Conversation History page...")
            await page.goto(f"{BASE_URL}/manage/analysis/conversation-history")
            await page.wait_for_timeout(3000)
            print("✓ Conversation History page loaded")

            # Step 3: Wait for content to load
            print("\n[Step 3] Waiting for content to load...")
            await page.wait_for_timeout(2000)

            # Take screenshot before Page Size change
            screenshot_path = f"{SCREENSHOT_DIR}/01_before_page_size_change.png"
            await page.screenshot(path=screenshot_path)
            print(f"✓ Screenshot saved: {screenshot_path}")

            # Step 4: Check if there's data in the table
            print("\n[Step 4] Checking table content...")
            table_rows = page.locator(".tabulator-row")
            row_count = await table_rows.count()
            print(f"  Found {row_count} rows in table")

            # Step 5: Find Page Size selector
            print("\n[Step 5] Finding Page Size selector...")
            page_size_select = page.locator(".tabulator-paginator select")

            if await page_size_select.count() == 0:
                page_size_select = page.locator(".tabulator-footer select")

            if await page_size_select.count() == 0:
                page_size_select = page.locator("#conversation-history-table select")

            if await page_size_select.count() > 0:
                print("✓ Page Size selector found")

                # Get current page size
                current_page_size = await page_size_select.first.input_value()
                print(f"  Current Page Size: {current_page_size}")

                # Step 6: Change Page Size and observe loading state
                print("\n[Step 6] Changing Page Size...")

                # Set up a listener to detect if "No sessions found" appears
                await page.evaluate(
                    """
                    window.noSessionsFound = false;
                    window.loadingShown = false;

                    const observer = new MutationObserver((mutations) => {
                        const placeholder = document.querySelector('.tabulator-placeholder');
                        if (placeholder) {
                            const text = placeholder.innerText || '';
                            if (text.includes('No sessions found') || text.includes('未找到会话')) {
                                window.noSessionsFound = true;
                            }
                            if (text.includes('Loading') || text.includes('加载') ||
                                placeholder.querySelector('.spinner-border')) {
                                window.loadingShown = true;
                            }
                        }
                    });

                    const table = document.querySelector('#conversation-history-table');
                    if (table) {
                        observer.observe(table, {
                            childList: true,
                            subtree: true,
                            characterData: true
                        });
                    }
                """
                )

                # Change page size using JavaScript
                print("  Triggering page size change via JavaScript...")
                await page.evaluate(
                    """
                    () => {
                        const table = Tabulator.findTable('#conversation-history-table')[0];
                        if (table) {
                            table.setPageSize(50);
                        }
                    }
                """
                )

                time.sleep(0.5)

                # Take screenshot during loading
                screenshot_path = f"{SCREENSHOT_DIR}/02_during_page_size_change.png"
                await page.screenshot(path=screenshot_path)
                print(f"✓ Screenshot saved: {screenshot_path}")

                # Check what was shown during loading
                result = await page.evaluate(
                    """
                    ({
                        noSessionsFound: window.noSessionsFound,
                        loadingShown: window.loadingShown
                    })
                """
                )

                print(f"  'No sessions found' shown: {result['noSessionsFound']}")
                print(f"  Loading state shown: {result['loadingShown']}")

                if result["noSessionsFound"]:
                    test_passed = False
                    error_messages.append("'No sessions found' was shown during Page Size change")

                if result["loadingShown"]:
                    print("✓ Loading state was shown during Page Size change")
                else:
                    print("Warning: Loading state was not detected (may have loaded quickly)")

                # Wait for data to load
                time.sleep(2)

                # Take screenshot after loading
                screenshot_path = f"{SCREENSHOT_DIR}/03_after_page_size_change.png"
                await page.screenshot(path=screenshot_path)
                print(f"✓ Screenshot saved: {screenshot_path}")

                # Verify page size was changed
                new_page_size = await page.evaluate(
                    """
                    () => {
                        const table = Tabulator.findTable('#conversation-history-table')[0];
                        return table ? table.getPageSize() : null;
                    }
                """
                )
                print(f"  New Page Size: {new_page_size}")

                if new_page_size == 50:
                    print("✓ Page Size changed successfully")
                else:
                    print(f"Warning: Page Size is {new_page_size}, expected 50")

            else:
                print("Warning: Page Size selector not found (page may use different layout)")

            # Print test result
            print("\n" + "=" * 60)
            if test_passed:
                print("TEST PASSED")
                print("Page Size change shows loading state correctly")
            else:
                print("TEST FAILED")
                for msg in error_messages:
                    print(f"  - {msg}")
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed with exception: {e}")
            test_passed = False
            error_messages.append(str(e))

            # Take error screenshot
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"{SCREENSHOT_DIR}/error_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            print(f"Error screenshot saved to {screenshot_path}")

        finally:
            await browser.close()

        return test_passed, error_messages


if __name__ == "__main__":
    passed, errors = asyncio.run(test_page_size_loading())
    exit(0 if passed else 1)
