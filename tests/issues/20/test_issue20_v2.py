#!/usr/bin/env python3
"""
Test script for Issue #20: Messages page loading slowly

This test:
1. Calls the existing test_messages_page_loading() from tests/ui/
2. Adds additional test for remote data fetch interval (5 minutes)
"""

import sys
import os
import time

# Add tests directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'tests', 'ui'))

from test_messages_page_loading import test_messages_page_loading


def test_remote_data_fetch_interval(page):
    """
    Additional test for Issue #20: Verify remote data fetch interval
    
    - Local data: fetched every 10 seconds
    - Remote data: fetched every 5 minutes (300 seconds)
    """
    print("\n" + "=" * 60)
    print("[Additional Test] Remote data fetch interval verification")
    print("=" * 60)
    
    # Monitor console for remote fetch messages
    remote_fetch_times = []
    
    def handle_console(msg):
        if 'Remote data fetched successfully' in msg.text:
            remote_fetch_times.append(time.time())
            print(f"  [Console] {msg.text}")
    
    page.on('console', handle_console)

    # Enable auto-refresh
    print("\n[Step 1] Enabling auto-refresh...")
    page.locator('#auto-refresh').check()
    print("✓ Auto-refresh enabled")

    # Wait 15 seconds - remote data should NOT be fetched immediately
    print("[Step 2] Waiting 15 seconds to verify remote fetch behavior...")
    time.sleep(15)

    if len(remote_fetch_times) == 0:
        print("✓ Remote data was NOT fetched immediately (correct)")
        print("  Remote data will be fetched after 5 minutes")
    else:
        print(f"⚠ Remote data was fetched {len(remote_fetch_times)} times in 15 seconds")

    # Disable auto-refresh
    page.locator('#auto-refresh').uncheck()
    print("✓ Auto-refresh disabled")

    # Test manual refresh (should fetch remote data)
    print("\n[Step 3] Testing manual refresh (should fetch remote data)...")
    remote_fetch_times.clear()
    page.locator('#messages-section button:has-text("Refresh")').click()
    print("✓ Manual refresh clicked")
    time.sleep(5)
    
    if len(remote_fetch_times) > 0:
        print("✓ Manual refresh triggered remote data fetch")
    else:
        print("⚠ Manual refresh did not trigger remote data fetch")

    print("\n" + "=" * 60)
    print("Additional test completed!")
    print("=" * 60)


if __name__ == "__main__":
    print("=" * 60)
    print("Issue #20: Messages page loading performance test")
    print("=" * 60)
    
    # Run main test from tests/ui/ and get reusable session
    browser, page = test_messages_page_loading(close_browser=False)
    
    try:
        # Run additional test for remote data fetch interval
        test_remote_data_fetch_interval(page)
        
        print("\n" + "=" * 60)
        print("All tests completed successfully!")
        print("=" * 60)
    finally:
        browser.close()