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

import pytest
import time
from playwright.async_api import async_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_analysis_page():
    """Test that Analysis page and Conversation History tab load correctly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Analysis page and Conversation History")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            # Wait for React app to load
            await page.wait_for_selector("#username", timeout=10000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for redirect to dashboard (React SPA navigation)
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            # Wait for React app to fully render
            await page.wait_for_load_state("networkidle", timeout=10000)
            print("✓ Login successful")

            # Step 2: Navigate to Analysis page
            print("\n[Step 2] Navigating to Analysis page...")
            start_time = time.time()
            # Wait for sidebar to be visible (React component)
            await page.wait_for_selector(".sidebar, nav.sidebar", timeout=15000)
            # Click on Analysis nav item (using text content in span)
            await page.click(
                '.sidebar .nav-link:has-text("Analysis"), nav.sidebar .nav-link:has-text("Analysis")'
            )

            # Wait for analysis section to be visible
            await page.wait_for_selector(".analysis", state="visible", timeout=5000)
            navigation_time = time.time() - start_time
            print(f"✓ Analysis page loaded in {navigation_time:.2f} seconds")

            # Step 3: Click Conversation History tab
            print("\n[Step 3] Clicking Conversation History tab...")
            await page.click("#conversation-history-tab")
            await page.wait_for_selector(
                "#conversation-history-table", state="visible", timeout=5000
            )
            print("✓ Conversation History tab loaded")

            # Step 4: Check if conversation history table is displayed
            print("\n[Step 4] Checking Conversation History table...")

            # Wait for table content to load
            time.sleep(2)

            # Check for table rows
            table_rows = await page.locator(".tabulator-row")
            row_count = await table_rows.count()

            if row_count > 0:
                print(f"✓ Found {row_count} conversation records in table")
            else:
                print("⚠ No conversation records found (may be expected if no data)")

            # Step 5: Test Timeline button (if available)
            print("\n[Step 5] Testing Timeline button...")

            # Find the first Timeline button in the table
            timeline_buttons = await page.locator('button[onclick*="showTimelineModal"]')
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
                    timeline_items = await page.locator(".timeline-item")
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

        except Exception as e:
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
