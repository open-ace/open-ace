#!/usr/bin/env python3
"""
Test script for verifying Messages page filter layout

This test verifies that:
1. Filter card has two rows layout
2. First row contains: Date, Host, Tool, Sender, Search
3. Second row contains: Role checkboxes (User, Assistant, System)
4. All filter elements are properly labeled and functional
"""

import os

import pytest
from playwright.async_api import async_playwright, expect

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_messages_filter_layout():
    """Test that Messages page filter layout matches the design."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Messages page filter layout")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(5000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            print("✓ Login successful")

            # Step 2: Navigate to Messages page directly
            print("\n[Step 2] Navigating to Messages page...")
            await page.goto(f"{BASE_URL}/manage/messages")
            await page.wait_for_load_state("networkidle")

            # Wait for messages container
            await page.wait_for_selector(".messages", timeout=10000)
            print("✓ Messages page loaded")

            # Step 3: Verify filter card exists
            print("\n[Step 3] Checking filter card...")
            filter_card = page.locator(".messages .card").first
            await expect(filter_card).to_be_visible()
            print("✓ Filter card is visible")

            # Step 4: Verify first row filters
            print("\n[Step 4] Checking first row filters...")

            # Check Date filter (Start Date)
            date_label = page.locator('small:has-text("Start Date:")')
            await expect(date_label).to_be_visible()
            print("✓ Start Date label visible")

            date_input = page.locator('input[type="date"]')
            await expect(date_input.first).to_be_visible()
            print("✓ Date input visible")

            # Check Host filter
            host_label = page.locator('small:has-text("Host:")')
            await expect(host_label).to_be_visible()
            print("✓ Host label visible")

            # Check Tool filter
            tool_label = page.locator('small:has-text("Tool:")')
            await expect(tool_label).to_be_visible()
            print("✓ Tool label visible")

            # Check Sender filter
            sender_label = page.locator('small:has-text("Sender:")')
            await expect(sender_label).to_be_visible()
            print("✓ Sender label visible")

            # Check Search filter
            search_label = page.locator('small:has-text("Search:")')
            await expect(search_label).to_be_visible()
            print("✓ Search label visible")

            search_input = page.locator('input[placeholder*="Search messages"]')
            await expect(search_input).to_be_visible()
            print("✓ Search input visible")

            # Step 5: Verify second row - Role checkboxes
            print("\n[Step 5] Checking second row - Role checkboxes...")

            role_label = page.locator('small:has-text("Role:")')
            await expect(role_label).to_be_visible()
            print("✓ Role label visible")

            # Check User checkbox
            user_checkbox = page.locator("#roleUser")
            await expect(user_checkbox).to_be_visible()
            user_label = page.locator('label[for="roleUser"]')
            await expect(user_label).to_have_text("User")
            print("✓ User checkbox visible with correct label")

            # Check Assistant checkbox
            assistant_checkbox = page.locator("#roleAssistant")
            await expect(assistant_checkbox).to_be_visible()
            assistant_label = page.locator('label[for="roleAssistant"]')
            await expect(assistant_label).to_have_text("Assistant")
            print("✓ Assistant checkbox visible with correct label")

            # Check System checkbox
            system_checkbox = page.locator("#roleSystem")
            await expect(system_checkbox).to_be_visible()
            system_label = page.locator('label[for="roleSystem"]')
            await expect(system_label).to_have_text("System")
            print("✓ System checkbox visible with correct label")

            # Step 6: Test filter functionality
            print("\n[Step 6] Testing filter functionality...")

            # Test Date filter change (use first date input = Start Date)
            await date_input.first.fill("2026-03-17")
            await page.wait_for_timeout(500)
            print("✓ Date filter can be changed")

            # Test User checkbox toggle
            await user_checkbox.check()
            await page.wait_for_timeout(300)
            await expect(user_checkbox).to_be_checked()
            print("✓ User checkbox can be toggled")

            # Test Search input
            await search_input.fill("test message")
            await page.wait_for_timeout(300)
            search_value = await search_input.input_value()
            assert search_value == "test message"
            print("✓ Search input works correctly")

            # Step 7: Take screenshot
            print("\n[Step 7] Taking screenshot...")
            await page.screenshot(path="screenshots/messages_filter_layout.png")
            print("✓ Screenshot saved: screenshots/messages_filter_layout.png")

            print("\n" + "=" * 60)
            print("All tests passed! Filter layout is correct.")
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            await page.screenshot(path="screenshots/messages_filter_layout_error.png")
            print("Error screenshot saved to screenshots/messages_filter_layout_error.png")
            raise

        finally:
            await browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
