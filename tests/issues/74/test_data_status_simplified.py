#!/usr/bin/env python3
"""
Test script for issue #74: Data Status panel simplified display.

Issue: Data Status panel shows unnecessary header row.

Fix: Remove header row with 'Data Status' text and refresh button.
"""

import sys
import os
import time
from datetime import datetime

# Get project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright, expect

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "74")
HEADLESS = True


def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    return path


def test_data_status_simplified():
    """Test #74: Data Status panel is simplified (no header)."""
    print("\n" + "=" * 50)
    print("Test #74: Data Status panel simplified display")
    print("=" * 50)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Login first
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("#login-btn")
            page.wait_for_url(f"{BASE_URL}/", timeout=10000)
            page.wait_for_load_state("networkidle")
            
            # Wait for data status to load
            page.wait_for_selector("#data-status-container", timeout=10000)
            time.sleep(1)
            
            # Take screenshot
            screenshot_path = take_screenshot(page, "01_data_status.png")
            print(f"  Screenshot: {screenshot_path}")
            
            # Check data status header is NOT present (simplified)
            header = page.locator("#data-status-container .data-status-header")
            header_count = header.count()
            assert header_count == 0, f"Data status header should not exist, found {header_count}"
            print("  ✓ Data status header removed (simplified)")
            
            # Check data status list exists
            status_list = page.locator("#data-status-container .data-status-list")
            expect(status_list).to_be_visible()
            print("  ✓ Data status list is visible")
            
            print("  ✓ Test #74 PASSED")
            return True
            
        except Exception as e:
            print(f"  ✗ Test #74 FAILED: {e}")
            take_screenshot(page, "error_74.png")
            return False
        finally:
            browser.close()


def main():
    """Run test."""
    print("\n" + "=" * 60)
    print(f"Issue #74 Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    result = test_data_status_simplified()
    
    print("\n" + "=" * 60)
    print(f"Result: {'✓ PASSED' if result else '✗ FAILED'}")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)