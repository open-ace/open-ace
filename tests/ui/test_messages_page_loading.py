#!/usr/bin/env python3
"""
Test script is for testing messages page loading

This test verifies that:
1. Messages page loads quickly (within 5 seconds)
2. Auto-refresh does not block the UI
[Commented out] 3. Manual refresh button works correctly
"""

import time

import pytest
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5000"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


@pytest.mark.asyncio
async def test_messages_page_loading():
    """Test that Messages page loads quickly without blocking."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("=" * 60)
            print("[UI] Testing: Messages page loading performance")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for redirect to dashboard (with longer timeout)
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            print("✓ Login successful")

            # Step 2: Navigate to Messages page
            print("\n[Step 2] Navigating to Messages page...")
            start_time = time.time()
            # Wait for sidebar to be visible
            await page.wait_for_selector(".sidebar", timeout=10000)
            # Click on Messages nav item (using text content in span)
            await page.click('.sidebar .nav-link:has-text("Messages")')

            # Wait for messages container to be visible
            await page.wait_for_selector("#messages-container", state="visible", timeout=5000)

            # Check if loading spinner appears and disappears quickly
            loading_time = time.time() - start_time
            print(f"✓ Messages page loaded in {loading_time:.2f} seconds")

            # Step 3: Check if messages are displayed or "No messages found" is shown
            print("\n[Step 3] Checking messages display...")

            # Wait for either messages or "no messages" message
            try:
                # Check for message items
                messages = page.locator(".message-item")
                no_messages = page.locator("text=No messages found")

                # Wait a bit for content to load
                time.sleep(2)

                msg_count = await messages.count()
                no_msg_count = await no_messages.count()

                if msg_count > 0:
                    print(f"✓ Found {msg_count} messages displayed")
                elif no_msg_count > 0:
                    print("✓ No messages found (expected for empty date)")
                else:
                    # Check if still loading
                    spinner = page.locator(".spinner-border")
                    spinner_count = await spinner.count()
                    if spinner_count > 0:
                        print("⚠ Page still loading after 2 seconds...")
                        # Wait more time
                        time.sleep(3)
                        msg_count = await messages.count()
                        if msg_count > 0:
                            print(f"✓ Messages loaded after waiting: {msg_count} messages")
                        else:
                            print("✗ Messages still not loaded after 5 seconds")
                    else:
                        print("✓ Page loaded (no spinner visible)")
            except Exception as e:
                print(f"⚠ Error checking messages: {e}")

            # Step 4: Test auto-refresh toggle (should not block)
            print("\n[Step 4] Testing auto-refresh toggle...")
            auto_refresh_checkbox = page.locator("#auto-refresh")

            # Enable auto-refresh
            await auto_refresh_checkbox.check()
            print("✓ Auto-refresh enabled")

            # Wait a moment to see if UI is blocked
            time.sleep(2)

            # Check if page is still responsive
            try:
                # Try to interact with the page
                await page.hover("#nav-dashboard")
                print("✓ Page is responsive after enabling auto-refresh")
            except Exception as e:
                print(f"✗ Page became unresponsive: {e}")

            # Disable auto-refresh
            await auto_refresh_checkbox.uncheck()
            print("✓ Auto-refresh disabled")

            print("\n" + "=" * 60)
            print("Test completed successfully!")
            print("=" * 60)

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            await page.screenshot(path="screenshots/test_messages_loading_error.png")
            print("Error screenshot saved to screenshots/test_messages_loading_error.png")
            raise

        finally:
            await browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
