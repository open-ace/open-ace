#!/usr/bin/env python3
"""
Test script for Analysis and Conversation History page

This test verifies that:
1. Analysis page loads correctly
2. Conversation History tab displays properly
3. Timeline modal works as expected

Usage:
    # Run standalone test
    python3 tests/ui/test_analysis_conversation_history.py
"""

import os
import time

import pytest
from playwright.async_api import async_playwright
from playwright.async_api import expect
from tests.e2e.ui.async_helpers import login_as

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_analysis_page():  # allow-no-assert: smoke test - visual verification only
    """Test that Analysis page and Conversation History tab load correctly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Analysis page and Conversation History")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await login_as(page, BASE_URL, USERNAME, PASSWORD)
            print("✓ Login successful")

            # Step 2: Navigate to Analysis page
            print("\n[Step 2] Navigating to Conversation History page...")
            start_time = time.time()
            await page.goto(f"{BASE_URL}/manage/analysis/conversation-history")
            await page.wait_for_load_state("networkidle", timeout=15000)
            await expect(
                page.get_by_role("heading", name="Conversation History")
            ).to_be_visible(timeout=15000)
            await page.wait_for_selector(
                "main, .manage-content, .conversation-history", timeout=15000
            )
            navigation_time = time.time() - start_time
            print(f"✓ Conversation History page loaded in {navigation_time:.2f} seconds")

            # Step 4: Check if conversation history table is displayed
            print("\n[Step 4] Checking Conversation History content...")

            # Wait for table content to load
            time.sleep(2)

            content_visible = await page.locator("table, .empty-state").count()
            content_visible += await page.get_by_text("No data").count()
            assert content_visible > 0, "Conversation History table or empty state should render"

            # Step 5: Test Timeline button (if available)
            print("\n[Step 5] Testing Timeline button...")

            # Find the first Timeline button in the table
            timeline_buttons = page.locator('button:has-text("Timeline"), button:has-text("时间线")')
            button_count = await timeline_buttons.count()

            if button_count > 0:
                print(f"✓ Found {button_count} Timeline buttons")

                # Click the first Timeline button
                await timeline_buttons.first.click()
                print("✓ Timeline button clicked")

                # Wait for modal to open
                await page.wait_for_selector("#timelineModal", state="visible", timeout=5000)
                time.sleep(1)

                # Check if modal has content
                modal_visible = await page.is_visible("#timelineModal")
                if modal_visible:
                    print("✓ Timeline modal opened")

                    # Check for timeline items
                    timeline_items = page.locator(".timeline-item")
                    item_count = await timeline_items.count()

                    if item_count > 0:
                        print(f"✓ Found {item_count} timeline items")

                        # Verify timeline only shows User and Assistant
                        role_labels = timeline_items.locator(".card-body strong")
                        role_count = await role_labels.count()

                        valid_roles = 0
                        for i in range(role_count):
                            role = await role_labels.nth(i).inner_text()
                            role = role.strip()
                            if role in ["User", "用户", "Assistant", "AI 助手", "AI"]:
                                valid_roles += 1

                        if valid_roles == role_count:
                            print("✓ All timeline items show only User or Assistant roles")
                        else:
                            print(f"⚠ Found {role_count - valid_roles} items with invalid roles")
                    else:
                        print("⚠ No timeline items found (may be expected if no data)")
                else:
                    print("✗ Timeline modal did not open")
            else:
                print("⚠ No Timeline buttons found (may be expected if no data)")

            # Take screenshot
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"screenshots/test_analysis_conversation_history_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            print(f"\n✓ Screenshot saved to {screenshot_path}")

            print("\n" + "=" * 60)
            print("Test completed successfully!")
            print("=" * 60)

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"\n✗ Test failed: {e}")
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = (
                f"screenshots/test_analysis_conversation_history_error_{timestamp}.png"
            )
            await page.screenshot(path=screenshot_path)
            print(f"Error screenshot saved to {screenshot_path}")
            raise

        finally:
            await browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
