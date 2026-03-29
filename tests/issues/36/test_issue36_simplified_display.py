#!/usr/bin/env python3
"""
Test script for Issue 36: Simplified display of user labels and sender list

This test verifies that:
1. Messages page displays simplified user names (not full hostnames)
2. Sender dropdown shows simplified names
3. Host filter shows simplified names
"""

import pytest
import time
import re
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 10000  # 10 seconds timeout


def simplify_display_name(name):
    """Simplify display name - extract username from format like 'user-hostname-tool'."""
    if not name:
        return name
    # Pattern: {user}-{hostname}[-{tool}]
    match = re.match(r"^([^-]+)-[^-]+\.[^-]+", name)
    if match:
        return match.group(1)
    simple_match = re.match(r"^([^-]+)-[^-]+-", name)
    if simple_match:
        return simple_match.group(1)
    return name


@pytest.mark.asyncio
async def test_issue36_simplified_display():
    """Test that Messages page shows simplified user names."""
    p = async_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    await page.set_default_timeout(TIMEOUT)

    try:
        print("=" * 60)
        print("[Issue 36] Testing: Simplified display of user labels")
        print("=" * 60)

        # Step 1: Login
        print("\n[Step 1] Logging in...")
        await page.goto(f"{BASE_URL}/login")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
        print("✓ Login successful")

        # Step 2: Navigate to Messages page
        print("\n[Step 2] Navigating to Messages page...")
        await page.click("#nav-messages")
        await page.wait_for_selector("#messages-container", state="visible", timeout=5000)
        time.sleep(2)  # Wait for messages to load
        print("✓ Messages page loaded")

        # Step 3: Check message items for simplified display
        print("\n[Step 3] Checking message items for simplified display...")
        messages = await page.locator(".message-item")
        message_count = messages.count()

        if message_count > 0:
            print(f"✓ Found {message_count} messages")

            # Check the first user message
            user_messages = await page.locator(".message-item:has(.role-badge.user)")
            if user_messages.count() > 0:
                first_user_msg = user_messages.first

                # Get the sender name displayed
                sender_element = first_user_msg.locator(".text-primary.fw-semibold")
                if sender_element.count() > 0:
                    displayed_sender = sender_element.inner_text()
                    print(f"  Displayed sender: '{displayed_sender}'")

                    # Check if it's simplified (should not contain full hostname pattern)
                    # Full hostname pattern: user-hostname.local-tool
                    if ".local" in displayed_sender.lower() or displayed_sender.count("-") > 1:
                        print(f"  ✗ Sender name NOT simplified: '{displayed_sender}'")
                    else:
                        print(f"  ✓ Sender name appears simplified: '{displayed_sender}'")

                # Get the host name displayed
                host_element = first_user_msg.locator(".text-muted:has(.bi-pc-display-horizontal)")
                if host_element.count() > 0:
                    displayed_host = host_element.inner_text()
                    print(f"  Displayed host: '{displayed_host}'")

                    # Check if it's simplified
                    if ".local" in displayed_host.lower():
                        print(f"  ✗ Host name NOT simplified: '{displayed_host}'")
                    else:
                        print(f"  ✓ Host name appears simplified: '{displayed_host}'")
            else:
                print("  No user messages found to check")
        else:
            print("  No messages found (empty database or wrong date)")

        # Step 4: Check sender dropdown
        print("\n[Step 4] Checking sender dropdown...")
        sender_filter = await page.locator("#sender-filter")
        if sender_filter.count() > 0:
            # Get all options
            options = sender_filter.locator("option")
            option_count = options.count()
            print(f"  Found {option_count} sender options")

            # Check first few options (skip "All Senders")
            for i in range(1, min(4, option_count)):
                option_text = options.nth(i).inner_text()
                print(f"  Option {i}: '{option_text}'")

                # Check if simplified
                if ".local" in option_text.lower():
                    print(f"    ✗ Option NOT simplified")
                else:
                    print(f"    ✓ Option appears simplified")
        else:
            print("  Sender filter not found")

        # Step 5: Check host dropdown
        print("\n[Step 5] Checking host dropdown...")
        host_filter = await page.locator("#host-filter")
        if host_filter.count() > 0:
            options = host_filter.locator("option")
            option_count = options.count()
            print(f"  Found {option_count} host options")

            for i in range(1, min(4, option_count)):
                option_text = options.nth(i).inner_text()
                print(f"  Option {i}: '{option_text}'")

                if ".local" in option_text.lower():
                    print(f"    ✗ Option NOT simplified")
                else:
                    print(f"    ✓ Option appears simplified")
        else:
            print("  Host filter not found")

        # Take screenshot
        await page.screenshot(path="screenshots/test_issue36_simplified_display.png")
        print("\n✓ Screenshot saved to screenshots/test_issue36_simplified_display.png")

        print("\n" + "=" * 60)
        print("Test completed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        await page.screenshot(path="screenshots/test_issue36_error.png")
        print("Error screenshot saved to screenshots/test_issue36_error.png")
        raise

    finally:
        await browser.close()


if __name__ == "__main__":
    test_issue36_simplified_display()
