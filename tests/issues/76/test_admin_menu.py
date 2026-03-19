#!/usr/bin/env python3
"""
Test script for issue #76: Admin menu shows Workspace and Report.

Issue: Admin login doesn't show Workspace and My Usage Report menu items.

Fix: Ensure Workspace and Report are visible to all logged-in users.
"""

import sys
import os
from datetime import datetime

# Get project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright, expect

# Configuration
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "76")
HEADLESS = True


def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    return path


def test_admin_menu():
    """Test #76: Admin menu shows Workspace and Report."""
    print("\n" + "=" * 50)
    print("Test #76: Admin menu shows Workspace and Report")
    print("=" * 50)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to login page
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")
            
            # Login
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("#login-btn")
            
            # Wait for redirect to home
            page.wait_for_url(f"{BASE_URL}/", timeout=10000)
            page.wait_for_load_state("networkidle")
            
            # Take screenshot after login
            screenshot_path = take_screenshot(page, "01_after_login.png")
            print(f"  Screenshot: {screenshot_path}")
            
            # Check Workspace menu is visible
            workspace_link = page.locator("#nav-workspace")
            expect(workspace_link).to_be_visible()
            print("  ✓ Workspace menu is visible")
            
            # Check Report menu is visible
            report_link = page.locator("#nav-report")
            expect(report_link).to_be_visible()
            print("  ✓ Report menu is visible")
            
            # Check Dashboard menu is visible (admin only)
            dashboard_link = page.locator("#nav-dashboard")
            expect(dashboard_link).to_be_visible()
            print("  ✓ Dashboard menu is visible (admin)")
            
            # Check Messages menu is visible (admin only)
            messages_link = page.locator("#nav-messages")
            expect(messages_link).to_be_visible()
            print("  ✓ Messages menu is visible (admin)")
            
            print("  ✓ Test #76 PASSED")
            return True
            
        except Exception as e:
            print(f"  ✗ Test #76 FAILED: {e}")
            take_screenshot(page, "error_76.png")
            return False
        finally:
            browser.close()


def main():
    """Run test."""
    print("\n" + "=" * 60)
    print(f"Issue #76 Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    result = test_admin_menu()
    
    print("\n" + "=" * 60)
    print(f"Result: {'✓ PASSED' if result else '✗ FAILED'}")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)