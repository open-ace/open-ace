#!/usr/bin/env python3
"""
Test script for Issue #72: Conversation History Fullscreen Mode

This test verifies that:
1. Conversation History page loads correctly
2. Fullscreen button is visible
3. Fullscreen mode displays content correctly (no blank area at bottom)
4. Exit fullscreen works correctly

Usage:
    python3 tests/issues/72/test_issue72.py
"""

import pytest
import time
import os
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 30000  # 30 seconds timeout
SCREENSHOT_DIR = "screenshots/issues/72"


@pytest.mark.asyncio
async def test_fullscreen():
    """Test Conversation History fullscreen functionality."""

    # Create screenshot directory
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    p = async_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    # Set default timeout
    await page.set_default_timeout(TIMEOUT)

    results = []

    try:
        print("=" * 60)
        print("Issue #72: Conversation History Fullscreen Mode Test")
        print("=" * 60)

        # Step 1: Login
        print("\n[Step 1] Logging in...")
        context.clear_cookies()  # Clear any existing session

        await page.goto(f"{BASE_URL}/login")
        await page.wait_for_load_state("networkidle")

        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')

        # Wait for navigation to complete
        await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("   ✓ Login successful")
        results.append(("Login", True, ""))

        # Step 2: Navigate to Analysis page
        print("\n[Step 2] Navigating to Analysis page...")
        await page.evaluate('switchSection("analysis")')
        time.sleep(2)
        print("   ✓ Analysis page loaded")
        results.append(("Navigate to Analysis", True, ""))

        # Step 3: Click Conversation History tab
        print("\n[Step 3] Clicking Conversation History tab...")
        await page.evaluate('document.getElementById("conversation-history-tab").click()')
        time.sleep(3)

        # Wait for table to initialize
        for i in range(10):
            table_exists = await page.evaluate('typeof conversationHistoryTable !== "undefined"')
            if table_exists:
                break
            time.sleep(1)

        print("   ✓ Conversation History tab loaded")
        results.append(("Conversation History tab", True, ""))

        # Take screenshot before fullscreen
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot_before = f"{SCREENSHOT_DIR}/before_fullscreen_{timestamp}.png"
        await page.screenshot(path=screenshot_before)
        print(f"   ✓ Screenshot saved: {screenshot_before}")

        # Step 4: Check fullscreen button
        print("\n[Step 4] Checking fullscreen button...")
        fullscreen_btn = await page.locator("#conversationHistoryFullscreenBtn")
        if fullscreen_btn.is_visible():
            print("   ✓ Fullscreen button is visible")
            results.append(("Fullscreen button visible", True, ""))
        else:
            print("   ✗ Fullscreen button not visible")
            results.append(("Fullscreen button visible", False, "Button not visible"))

        # Step 5: Enter fullscreen mode
        print("\n[Step 5] Entering fullscreen mode...")
        await page.evaluate("toggleConversationHistoryFullscreen()")
        time.sleep(1)

        # Take screenshot in fullscreen mode
        screenshot_fullscreen = f"{SCREENSHOT_DIR}/fullscreen_mode_{timestamp}.png"
        await page.screenshot(path=screenshot_fullscreen)
        print(f"   ✓ Screenshot saved: {screenshot_fullscreen}")

        # Step 6: Verify fullscreen mode
        print("\n[Step 6] Verifying fullscreen mode...")

        # Check container styles
        container = await page.locator("#conversation-history-table-container")
        container_style = container.evaluate("el => el.style.cssText")

        # Check if container has fullscreen styles
        if "position: fixed" in container_style and "100vh" in container_style:
            print("   ✓ Container has fullscreen styles")
            results.append(("Fullscreen styles applied", True, ""))
        else:
            print(f"   ✗ Container styles incorrect: {container_style[:100]}...")
            results.append(("Fullscreen styles applied", False, container_style[:100]))

        # Check table height
        table_height = await page.evaluate(
            'document.querySelector("#conversation-history-table").offsetHeight'
        )
        viewport_height = await page.evaluate("window.innerHeight")

        print(f"   Table height: {table_height}px, Viewport height: {viewport_height}px")

        # Table should be close to viewport height (minus some padding)
        expected_min_height = viewport_height - 100  # Allow for padding
        if table_height >= expected_min_height:
            print(f"   ✓ Table height is appropriate ({table_height}px >= {expected_min_height}px)")
            results.append(("Table fills fullscreen", True, f"Height: {table_height}px"))
        else:
            print(f"   ✗ Table height too small ({table_height}px < {expected_min_height}px)")
            results.append(
                (
                    "Table fills fullscreen",
                    False,
                    f"Height: {table_height}px, expected >= {expected_min_height}px",
                )
            )

        # Check for visible table rows
        rows = await page.locator("#conversation-history-table .tabulator-row")
        row_count = rows.count()

        print(f"   Table rows: {row_count}")

        if row_count > 0:
            print(f"   ✓ Table has {row_count} rows")
            results.append(("Table rows visible", True, f"{row_count} rows"))
        else:
            print("   ⚠ No rows (may be expected if no data)")
            results.append(("Table rows visible", True, "No data"))

        # Step 7: Exit fullscreen mode
        print("\n[Step 7] Exiting fullscreen mode...")

        # Click the exit fullscreen button
        await page.evaluate("toggleConversationHistoryFullscreen()")
        time.sleep(1)

        # Take screenshot after exiting fullscreen
        screenshot_exit = f"{SCREENSHOT_DIR}/after_fullscreen_{timestamp}.png"
        await page.screenshot(path=screenshot_exit)
        print(f"   ✓ Screenshot saved: {screenshot_exit}")

        # Verify container styles are reset
        container_style_after = container.evaluate("el => el.style.cssText")
        if not container_style_after or "fixed" not in container_style_after:
            print("   ✓ Container styles reset")
            results.append(("Exit fullscreen", True, ""))
        else:
            print(f"   ✗ Container styles not reset: {container_style_after[:100]}...")
            results.append(("Exit fullscreen", False, "Styles not reset"))

        # Print summary
        print("\n" + "=" * 60)
        print("Test Summary")
        print("=" * 60)

        passed = sum(1 for r in results if r[1])
        failed = sum(1 for r in results if not r[1])

        for name, success, detail in results:
            status = "✓ PASS" if success else "✗ FAIL"
            print(f"  {status}: {name}")
            if detail and not success:
                print(f"         Detail: {detail}")

        print(f"\nTotal: {passed} passed, {failed} failed")
        print("=" * 60)

        return failed == 0

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback

        traceback.print_exc()

        # Take error screenshot
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot_error = f"{SCREENSHOT_DIR}/error_{timestamp}.png"
        await page.screenshot(path=screenshot_error)
        print(f"Error screenshot saved: {screenshot_error}")

        return False

    finally:
        await browser.close()


if __name__ == "__main__":
    success = test_fullscreen()
    exit(0 if success else 1)
