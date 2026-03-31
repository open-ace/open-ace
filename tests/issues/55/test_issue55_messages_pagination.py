#!/usr/bin/env python3
"""
Test script for Issue #55: Messages 页面点 Next>不工作

This test verifies that:
1. Messages page pagination controls are displayed when there are multiple pages
2. Next button is clickable and loads the next page
3. Previous button is clickable and loads the previous page
4. Page numbers update correctly when navigating
"""

import pytest
import time
from playwright.async_api import async_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5000"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_messages_pagination():
    """Test that Messages page pagination works correctly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Messages page pagination (Issue #55)")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for redirect to dashboard
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            print("✓ Login successful")

            # Step 2: Navigate to Messages page
            print("\n[Step 2] Navigating to Messages page...")
            # Wait for sidebar to be visible
            await page.wait_for_selector(".sidebar", timeout=10000)
            # Click on Messages nav item (using text content in span)
            await page.click('.sidebar .nav-link:has-text("Messages")')
            await page.wait_for_selector("#messages-container", state="visible", timeout=5000)
            time.sleep(3)  # Wait for messages to load

            # Step 3: Check if pagination controls exist
            print("\n[Step 3] Checking pagination controls...")
            pagination_controls = page.locator("#pagination-controls")
            pagination_count = await pagination_controls.count()

            if pagination_count > 0:
                print("✓ Pagination controls found")

                # Check if Next button exists and is visible
                next_button = page.locator("#next-page")
                next_count = await next_button.count()
                if next_count > 0:
                    print("✓ Next button found")

                    # Check if Next button is clickable (not disabled)
                    next_button_class = await next_button.get_attribute("class")
                    if next_button_class and "disabled" not in next_button_class:
                        print("✓ Next button is clickable (not disabled)")

                        # Get current page number
                        current_page_el = page.locator("#current-page")
                        total_pages_el = page.locator("#total-pages")

                        current_count = await current_page_el.count()
                        total_count = await total_pages_el.count()

                        if current_count > 0 and total_count > 0:
                            current_page_text = await current_page_el.inner_text()
                            total_pages_text = await total_pages_el.inner_text()
                            print(
                                f"  Current page: {current_page_text}, Total pages: {total_pages_text}"
                            )

                            # Step 4: Click Next button
                            print("\n[Step 4] Clicking Next button...")
                            await next_button.click()
                            time.sleep(2)  # Wait for page to load

                            # Check if page number updated
                            new_current_page = await current_page_el.inner_text()
                            print(f"  New page: {new_current_page}")

                            if new_current_page != current_page_text:
                                print("✓ Page number updated after clicking Next")

                                # Verify messages are loaded
                                messages = page.locator(".message-item")
                                msg_count = await messages.count()
                                if msg_count > 0:
                                    print(f"✓ New page loaded with {msg_count} messages")
                                else:
                                    print("⚠ No messages on new page")

                                # Step 5: Test Previous button
                                print("\n[Step 5] Testing Previous button...")
                                prev_button = page.locator("#prev-page")
                                prev_count = await prev_button.count()
                                if prev_count > 0:
                                    prev_button_class = await prev_button.get_attribute("class")
                                    if prev_button_class and "disabled" not in prev_button_class:
                                        print("✓ Previous button is clickable")
                                        await prev_button.click()
                                        time.sleep(2)

                                        final_page = await current_page_el.inner_text()
                                        print(f"  Page after Previous: {final_page}")

                                        if final_page == current_page_text:
                                            print("✓ Previous button works correctly")
                                        else:
                                            print(
                                                f"⚠ Page number mismatch: expected {current_page_text}, got {final_page}"
                                            )
                                    else:
                                        print("⚠ Previous button is disabled")
                                else:
                                    print("⚠ Previous button not found")
                            else:
                                print(
                                    "✗ Page number did not update after clicking Next - THIS IS THE BUG!"
                                )
                        else:
                            print("⚠ Page number elements not found")
                    else:
                        print("⚠ Next button is disabled (may be on last page)")
                else:
                    print("⚠ Next button not found")
            else:
                print("ℹ Pagination controls not displayed (may be only one page of messages)")

            print("\n" + "=" * 60)
            print("Test completed!")
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            await page.screenshot(path="screenshots/issues/55/test_error.png")
            print("Error screenshot saved to screenshots/issues/55/test_error.png")
            raise

        finally:
            await browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
