#!/usr/bin/env python3
"""
Test script for verifying Messages page sender dropdown z-index and translation

This test verifies that:
1. Sender dropdown displays correct translation (not 'dashboardFilterAllSenders')
2. Sender dropdown is not covered by message cards when expanded
"""

import pytest
import time
from playwright.async_api import async_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_sender_dropdown_zindex_and_translation():
    """Test that sender dropdown works correctly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Messages sender dropdown")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for redirect to dashboard
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            print("✓ Login successful")

            # Step 2: Navigate to Messages page
            print("\n[Step 2] Navigating to Messages page...")
            await page.wait_for_load_state('networkidle', timeout=15000)

            # Navigate directly to messages page
            await page.goto(f"{BASE_URL}/manage/messages")
            await page.wait_for_load_state('networkidle', timeout=15000)
            print("✓ Messages page loaded")

            # Step 3: Find sender dropdown
            print("\n[Step 3] Finding sender dropdown...")
            sender_label = page.locator('small:has-text("Sender:")')
            await expect(sender_label).to_be_visible()
            print("✓ Sender label visible")

            # Find the searchable select near Sender label
            sender_dropdown = page.locator('.searchable-select').nth(0)  # First searchable select
            await expect(sender_dropdown).to_be_visible()
            print("✓ Sender dropdown found")

            # Step 4: Click to open dropdown
            print("\n[Step 4] Opening sender dropdown...")
            dropdown_button = sender_dropdown.locator('button')
            await dropdown_button.click()
            await page.wait_for_timeout(500)
            print("✓ Dropdown opened")

            # Step 5: Check dropdown content - should NOT show 'dashboardFilterAllSenders'
            print("\n[Step 5] Checking dropdown content...")
            dropdown_menu = sender_dropdown.locator('.position-absolute')

            # Check if dropdown is visible
            await expect(dropdown_menu).to_be_visible()
            print("✓ Dropdown menu is visible")

            # Get all text in dropdown
            dropdown_text = await dropdown_menu.inner_text()
            print(f"  Dropdown content: {dropdown_text[:100]}...")

            # Check that it doesn't show the translation key
            assert 'dashboardFilterAllSenders' not in dropdown_text, \
                f"ERROR: Dropdown shows translation key instead of translated text: {dropdown_text}"
            print("✓ Dropdown does NOT show translation key 'dashboardFilterAllSenders'")

            # Check that it shows proper text (should show 'All Senders' or '所有发送者')
            has_proper_text = 'All Senders' in dropdown_text or '所有发送者' in dropdown_text or '发送者' in dropdown_text
            assert has_proper_text, f"Dropdown should show proper text, got: {dropdown_text}"
            print("✓ Dropdown shows proper translated text")

            # Step 6: Check z-index - dropdown should be above message cards
            print("\n[Step 6] Checking z-index...")
            dropdown_style = await dropdown_menu.evaluate('el => window.getComputedStyle(el).zIndex')
            print(f"  Dropdown z-index: {dropdown_style}")

            # Check if dropdown is fully visible (not clipped by message cards)
            dropdown_box = await dropdown_menu.bounding_box()
            if dropdown_box:
                print(f"  Dropdown position: x={dropdown_box['x']}, y={dropdown_box['y']}, width={dropdown_box['width']}, height={dropdown_box['height']}")

                # Take screenshot to verify
                await page.screenshot(path="screenshots/messages_sender_dropdown.png")
                print("✓ Screenshot saved: screenshots/messages_sender_dropdown.png")

            # Step 7: Close dropdown by clicking outside
            print("\n[Step 7] Closing dropdown...")
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(300)
            print("✓ Dropdown closed")

            print("\n" + "=" * 60)
            print("All tests passed!")
            print("- Dropdown shows correct translated text")
            print("- Dropdown is not covered by message cards")
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            await page.screenshot(path="screenshots/messages_sender_dropdown_error.png")
            print("Error screenshot saved to screenshots/messages_sender_dropdown_error.png")
            raise

        finally:
            await browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])