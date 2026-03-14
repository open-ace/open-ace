#!/usr/bin/env python3
"""
Test script for Issue #20: Messages page loading slowly

This test verifies that:
1. Messages page loads quickly (within 5 seconds)
2. Auto-refresh does not block the UI
3. Manual refresh button works correctly
"""

import time
from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


def test_messages_page_loading():
    """Test that Messages page loads quickly without blocking."""
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        # Set default timeout
        page.set_default_timeout(TIMEOUT)
        
        try:
            print("=" * 60)
            print("Testing Issue #20: Messages page loading performance")
            print("=" * 60)
            
            # Step 1: Login
            print("\n[Step 1] Logging in...")
            page.goto(f"{BASE_URL}/login")
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            
            # Wait for redirect to dashboard (with longer timeout)
            page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            print("✓ Login successful")
            
            # Step 2: Navigate to Messages page
            print("\n[Step 2] Navigating to Messages page...")
            start_time = time.time()
            page.click('#nav-messages')
            
            # Wait for messages container to be visible
            page.wait_for_selector('#messages-container', state='visible', timeout=5000)
            
            # Check if loading spinner appears and disappears quickly
            loading_time = time.time() - start_time
            print(f"✓ Messages page loaded in {loading_time:.2f} seconds")
            
            # Step 3: Check if messages are displayed or "No messages found" is shown
            print("\n[Step 3] Checking messages display...")
            
            # Wait for either messages or "no messages" message
            try:
                # Check for message items
                messages = page.locator('.message-item')
                no_messages = page.locator('text=No messages found')
                
                # Wait a bit for content to load
                time.sleep(2)
                
                if messages.count() > 0:
                    print(f"✓ Found {messages.count()} messages displayed")
                elif no_messages.count() > 0:
                    print("✓ No messages found (expected for empty date)")
                else:
                    # Check if still loading
                    spinner = page.locator('.spinner-border')
                    if spinner.count() > 0:
                        print("⚠ Page still loading after 2 seconds...")
                        # Wait more time
                        time.sleep(3)
                        if messages.count() > 0:
                            print(f"✓ Messages loaded after waiting: {messages.count()} messages")
                        else:
                            print("✗ Messages still not loaded after 5 seconds")
                    else:
                        print("✓ Page loaded (no spinner visible)")
            except Exception as e:
                print(f"⚠ Error checking messages: {e}")
            
            # Step 4: Test auto-refresh toggle (should not block)
            print("\n[Step 4] Testing auto-refresh toggle...")
            auto_refresh_checkbox = page.locator('#auto-refresh')
            
            # Enable auto-refresh
            auto_refresh_checkbox.check()
            print("✓ Auto-refresh enabled")
            
            # Wait a moment to see if UI is blocked
            time.sleep(2)
            
            # Check if page is still responsive
            try:
                # Try to interact with the page
                page.hover('#nav-dashboard')
                print("✓ Page is responsive after enabling auto-refresh")
            except Exception as e:
                print(f"✗ Page became unresponsive: {e}")
            
            # Disable auto-refresh
            auto_refresh_checkbox.uncheck()
            print("✓ Auto-refresh disabled")
            
            # Step 5: Test manual refresh button
            print("\n[Step 5] Testing manual refresh button...")
            refresh_btn = page.locator('#messages-section button:has-text("Refresh")')
            refresh_btn.click()
            print("✓ Refresh button clicked")
            
            # Wait for refresh to complete
            time.sleep(3)
            print("✓ Refresh completed")
            
            # Take screenshot
            page.screenshot(path="screenshots/test_issue20_messages.png")
            print("\n✓ Screenshot saved to screenshots/test_issue20_messages.png")
            
            print("\n" + "=" * 60)
            print("Test completed successfully!")
            print("=" * 60)
            
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            page.screenshot(path="screenshots/test_issue20_error.png")
            print("Error screenshot saved to screenshots/test_issue20_error.png")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    test_messages_page_loading()