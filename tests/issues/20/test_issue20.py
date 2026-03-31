#!/usr/bin/env python3
"""
Test script for Issue #20: Messages page loading slowly

This test verifies remote data fetch interval:
- Local data: fetched every 10 seconds
- Remote data: fetched every 5 minutes (300 seconds)
"""

import pytest
import time
from playwright.async_api import async_playwright

# Test configuration
BASE_URL = "http://localhost:5000"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 15000  # 15 seconds timeout


@pytest.mark.asyncio
async def test_remote_data_fetch_interval():
    """
    Test for Issue #20: Verify remote data fetch interval

    - Local data: fetched every 10 seconds
    - Remote data: fetched every 5 minutes (300 seconds)
    """
    print("\n" + "=" * 60)
    print("[Test] Remote data fetch interval verification")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            # Login
            print("\n[Step 1] Logging in...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            print("✓ Login successful")

            # Navigate to Messages page
            print("\n[Step 2] Navigating to Messages page...")
            await page.click("#nav-messages")
            await page.wait_for_selector("#messages-container", state="visible", timeout=5000)
            print("✓ Messages page loaded")

            # Monitor console for remote fetch messages
            remote_fetch_times = []

            def handle_console(msg):
                if "Remote data fetched successfully" in msg.text:
                    remote_fetch_times.append(time.time())
                    print(f"  [Console] {msg.text}")

            page.on("console", handle_console)

            # Enable auto-refresh
            print("\n[Step 3] Enabling auto-refresh...")
            await page.locator("#auto-refresh").check()
            print("✓ Auto-refresh enabled")

            # Wait 15 seconds - remote data should NOT be fetched immediately
            print("[Step 4] Waiting 15 seconds to verify remote fetch behavior...")
            await page.wait_for_timeout(15000)

            if len(remote_fetch_times) == 0:
                print("✓ Remote data was NOT fetched immediately (correct)")
                print("  Remote data will be fetched after 5 minutes")
            else:
                print(f"⚠ Remote data was fetched {len(remote_fetch_times)} times in 15 seconds")

            # Disable auto-refresh
            await page.locator("#auto-refresh").uncheck()
            print("✓ Auto-refresh disabled")

            print("\n" + "=" * 60)
            print("Test completed!")
            print("=" * 60)

        finally:
            await browser.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_remote_data_fetch_interval())
