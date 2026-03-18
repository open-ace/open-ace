#!/usr/bin/env python3
"""
Test script for Issue #55: Messages 页面点 Next>不工作

This test verifies that:
1. Messages page pagination controls are displayed when there are multiple pages
2. Next button is clickable and loads the next page
3. Previous button is clickable and loads the previous page
4. Page numbers update correctly when navigating
"""

import time
from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


def test_messages_pagination():
    """Test that Messages page pagination works correctly."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # Set default timeout
    page.set_default_timeout(TIMEOUT)

    try:
        print("=" * 60)
        print("[UI] Testing: Messages page pagination (Issue #55)")
        print("=" * 60)

        # Step 1: Login
        print("\n[Step 1] Logging in...")
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')

        # Wait for redirect to dashboard
        page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("✓ Login successful")

        # Step 2: Navigate to Messages page
        print("\n[Step 2] Navigating to Messages page...")
        page.click('#nav-messages')
        page.wait_for_selector('#messages-container', state='visible', timeout=5000)
        time.sleep(3)  # Wait for messages to load

        # Step 3: Check if pagination controls exist
        print("\n[Step 3] Checking pagination controls...")
        pagination_controls = page.locator('#pagination-controls')
        
        if pagination_controls.count() > 0:
            print("✓ Pagination controls found")
            
            # Check if Next button exists and is visible
            next_button = page.locator('#next-page')
            if next_button.count() > 0:
                print("✓ Next button found")
                
                # Check if Next button is clickable (not disabled)
                next_button_class = next_button.get_attribute('class')
                if 'disabled' not in next_button_class:
                    print("✓ Next button is clickable (not disabled)")
                    
                    # Get current page number
                    current_page_el = page.locator('#current-page')
                    total_pages_el = page.locator('#total-pages')
                    
                    if current_page_el.count() > 0 and total_pages_el.count() > 0:
                        current_page_text = current_page_el.inner_text()
                        total_pages_text = total_pages_el.inner_text()
                        print(f"  Current page: {current_page_text}, Total pages: {total_pages_text}")
                        
                        # Step 4: Click Next button
                        print("\n[Step 4] Clicking Next button...")
                        next_button.click()
                        time.sleep(2)  # Wait for page to load
                        
                        # Check if page number updated
                        new_current_page = current_page_el.inner_text()
                        print(f"  New page: {new_current_page}")
                        
                        if new_current_page != current_page_text:
                            print("✓ Page number updated after clicking Next")
                            
                            # Verify messages are loaded
                            messages = page.locator('.message-item')
                            if messages.count() > 0:
                                print(f"✓ New page loaded with {messages.count()} messages")
                            else:
                                print("⚠ No messages on new page")
                            
                            # Step 5: Test Previous button
                            print("\n[Step 5] Testing Previous button...")
                            prev_button = page.locator('#prev-page')
                            if prev_button.count() > 0:
                                prev_button_class = prev_button.get_attribute('class')
                                if 'disabled' not in prev_button_class:
                                    print("✓ Previous button is clickable")
                                    prev_button.click()
                                    time.sleep(2)
                                    
                                    final_page = current_page_el.inner_text()
                                    print(f"  Page after Previous: {final_page}")
                                    
                                    if final_page == current_page_text:
                                        print("✓ Previous button works correctly")
                                    else:
                                        print(f"⚠ Page number mismatch: expected {current_page_text}, got {final_page}")
                                else:
                                    print("⚠ Previous button is disabled")
                            else:
                                print("⚠ Previous button not found")
                        else:
                            print("✗ Page number did not update after clicking Next - THIS IS THE BUG!")
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
        page.screenshot(path="screenshots/issues/55/test_error.png")
        print("Error screenshot saved to screenshots/issues/55/test_error.png")
        browser.close()
        raise

    browser.close()


if __name__ == "__main__":
    test_messages_pagination()
