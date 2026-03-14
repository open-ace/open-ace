#!/usr/bin/env python3
"""
Screenshot script for Issue 36: Messages page simplified display
"""

import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
OUTPUT_DIR = "screenshots"


def take_screenshots():
    """Take screenshots of Messages page to verify simplified display."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    try:
        # Login
        print("Logging in...")
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("Login successful")

        # Navigate to Messages page
        print("Navigating to Messages page...")
        page.click('#nav-messages')
        page.wait_for_selector('#messages-container', state='visible', timeout=5000)
        time.sleep(2)  # Wait for messages to load
        print("Messages page loaded")

        # Take full page screenshot
        page.screenshot(path=f"{OUTPUT_DIR}/issue36_messages_full.png", full_page=True)
        print(f"Screenshot saved: {OUTPUT_DIR}/issue36_messages_full.png")

        # Take screenshot of first user message
        user_messages = page.locator('.message-item:has(.role-badge.user)')
        if user_messages.count() > 0:
            first_user_msg = user_messages.first
            first_user_msg.screenshot(path=f"{OUTPUT_DIR}/issue36_first_user_message.png")
            print(f"Screenshot saved: {OUTPUT_DIR}/issue36_first_user_message.png")

            # Get the displayed text for verification
            sender_element = first_user_msg.locator('.text-primary.fw-semibold')
            if sender_element.count() > 0:
                sender_text = sender_element.inner_text()
                print(f"Sender displayed: '{sender_text}'")

            host_element = first_user_msg.locator('.text-muted:has(.bi-pc-display-horizontal)')
            if host_element.count() > 0:
                host_text = host_element.inner_text()
                print(f"Host displayed: '{host_text}'")

        # Take screenshot of sender dropdown
        sender_filter = page.locator('#sender-filter')
        if sender_filter.count() > 0:
            # Click to open dropdown
            sender_filter.click()
            time.sleep(0.5)
            page.screenshot(path=f"{OUTPUT_DIR}/issue36_sender_dropdown.png")
            print(f"Screenshot saved: {OUTPUT_DIR}/issue36_sender_dropdown.png")

        print("\nScreenshots completed!")

    except Exception as e:
        print(f"Error: {e}")
        page.screenshot(path=f"{OUTPUT_DIR}/issue36_error.png")

    finally:
        browser.close()


if __name__ == "__main__":
    take_screenshots()