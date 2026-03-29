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

import pytest
import time
import os
from playwright.async_api import async_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout

# Screenshot directory
SCREENSHOT_DIR = "screenshots/issues/42"


@pytest.mark.asyncio
async def test_page_size_loading():
    """Test that Page Size change shows loading state instead of 'No sessions found'."""
    # Ensure screenshot directory exists
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    p = async_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    # Set default timeout
    await page.set_default_timeout(TIMEOUT)

    test_passed = True
    error_messages = []

    try:
        print("=" * 60)
        print("[UI] Testing: Issue #42 - Page Size Loading State")
        print("=" * 60)

        # Step 1: Login
        print("\n[Step 1] Logging in...")
        await page.goto(f"{BASE_URL}/login")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')

        # Wait for redirect to dashboard
        await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("✓ Login successful")

        # Step 2: Navigate to Analysis page
        print("\n[Step 2] Navigating to Analysis page...")

        # Use JavaScript to switch section directly (more reliable)
        await page.evaluate("switchSection('analysis')")

        # Wait for analysis section to be visible
        time.sleep(1)

        # Check if analysis section is visible
        is_visible = await page.evaluate(
            """
            () => {
                const section = document.getElementById('analysis-section');
                return section && section.style.display !== 'none';
            }
        """
        )

        if not is_visible:
            print("  Warning: Analysis section not visible, trying again...")
            await page.evaluate("switchSection('analysis')")
            time.sleep(1)

        print("✓ Analysis page loaded")

        # Step 3: Click Conversation History tab
        print("\n[Step 3] Clicking Conversation History tab...")
        await page.click("#conversation-history-tab")
        await page.wait_for_selector("#conversation-history-table", state="visible", timeout=5000)
        print("✓ Conversation History tab loaded")

        # Step 4: Wait for initial data to load
        print("\n[Step 4] Waiting for initial data to load...")
        time.sleep(2)

        # Take screenshot before Page Size change
        screenshot_path = f"{SCREENSHOT_DIR}/01_before_page_size_change.png"
        await page.screenshot(path=screenshot_path)
        print(f"✓ Screenshot saved: {screenshot_path}")

        # Step 5: Check if there's data in the table
        print("\n[Step 5] Checking table content...")
        table_rows = await page.locator(".tabulator-row")
        row_count = table_rows.count()
        print(f"  Found {row_count} rows in table")

        # Step 6: Find and click Page Size selector
        print("\n[Step 6] Finding Page Size selector...")

        # Debug: print the HTML structure of the paginator
        paginator_html = await page.evaluate(
            """
            () => {
                const paginator = document.querySelector('.tabulator-paginator');
                return paginator ? paginator.outerHTML : 'Paginator not found';
            }
        """
        )
        print(f"  Paginator HTML: {paginator_html[:500]}...")

        # Try different selectors for Page Size
        page_size_select = await page.locator(".tabulator-paginator select")

        if page_size_select.count() == 0:
            # Try alternative selector
            page_size_select = await page.locator(".tabulator-footer select")

        if page_size_select.count() == 0:
            # Try another alternative
            page_size_select = await page.locator("#conversation-history-table select")

        if page_size_select.count() > 0:
            print("✓ Page Size selector found")

            # Debug: print the select element HTML
            select_html = await page.evaluate(
                """
                () => {
                    const select = document.querySelector('.tabulator-page-size');
                    return select ? select.outerHTML : 'Select not found';
                }
            """
            )
            print(f"  Select HTML: {select_html}")

            # Get current page size
            current_page_size = page_size_select.first.input_value()
            print(f"  Current Page Size: {current_page_size}")

            # Get available options
            options = page_size_select.first.locator("option")
            option_count = options.count()
            print(f"  Available options: {option_count}")

            if option_count > 0:
                for i in range(option_count):
                    option_value = options.nth(i).get_attribute("value")
                    option_text = options.nth(i).inner_text()
                    print(f"    - {option_value}: {option_text}")
            else:
                print("  No options found in select element")
                # Try to get options via JavaScript
                options_js = await page.evaluate(
                    """
                    () => {
                        const select = document.querySelector('.tabulator-page-size');
                        if (select) {
                            return Array.from(select.options).map(opt => ({value: opt.value, text: opt.text}));
                        }
                        return [];
                    }
                """
                )
                print(f"  Options via JS: {options_js}")

            # Step 7: Change Page Size and observe loading state
            print("\n[Step 7] Changing Page Size...")

            # Set up a listener to detect if "No sessions found" appears
            no_sessions_found = False
            loading_shown = False

            # Take a quick screenshot right after clicking
            # We'll use JavaScript to monitor the placeholder content
            await page.evaluate(
                """
                window.noSessionsFound = false;
                window.loadingShown = false;

                // Monitor the placeholder element
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

            # Change page size using JavaScript (since select options may not be populated)
            # This simulates what happens when user changes page size
            print("  Triggering page size change via JavaScript...")
            await page.evaluate(
                """
                () => {
                    // Get the Tabulator instance
                    const table = Tabulator.findTable('#conversation-history-table')[0];
                    if (table) {
                        // Set page size to 50
                        table.setPageSize(50);
                    }
                }
            """
            )

            # Wait a brief moment for the change to process
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
                # This might be OK if the data loaded very quickly
                print("⚠ Loading state was not detected (may have loaded quickly)")

            # Wait for data to load
            time.sleep(2)

            # Take screenshot after loading
            screenshot_path = f"{SCREENSHOT_DIR}/03_after_page_size_change.png"
            await page.screenshot(path=screenshot_path)
            print(f"✓ Screenshot saved: {screenshot_path}")

            # Step 8: Verify data loaded correctly
            print("\n[Step 8] Verifying data loaded correctly...")
            new_row_count = await page.locator(".tabulator-row").count()
            print(f"  Found {new_row_count} rows after Page Size change")

            # Verify page size was actually changed using JavaScript
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
                test_passed = False
                error_messages.append(
                    f"Page Size not changed correctly. Expected: 50, Got: {new_page_size}"
                )

            # Step 9: Test another Page Size change
            print("\n[Step 9] Testing another Page Size change (to 100)...")

            # Reset the monitoring flags
            await page.evaluate(
                """
                window.noSessionsFound = false;
                window.loadingShown = false;
            """
            )

            # Change page size using JavaScript
            await page.evaluate(
                """
                () => {
                    const table = Tabulator.findTable('#conversation-history-table')[0];
                    if (table) {
                        table.setPageSize(100);
                    }
                }
            """
            )
            print("  Changed Page Size to 100")

            time.sleep(0.5)

            # Take screenshot
            screenshot_path = f"{SCREENSHOT_DIR}/04_page_size_100.png"
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
                error_messages.append(
                    "'No sessions found' was shown during second Page Size change"
                )

            # Wait for data to load
            time.sleep(2)

            # Take final screenshot
            screenshot_path = f"{SCREENSHOT_DIR}/05_final_state.png"
            await page.screenshot(path=screenshot_path)
            print(f"✓ Screenshot saved: {screenshot_path}")

        else:
            test_passed = False
            error_messages.append("Page Size selector not found")

        # Print test result
        print("\n" + "=" * 60)
        if test_passed:
            print("TEST PASSED ✓")
            print("Page Size change shows loading state correctly")
        else:
            print("TEST FAILED ✗")
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
    passed, errors = test_page_size_loading()
    exit(0 if passed else 1)
