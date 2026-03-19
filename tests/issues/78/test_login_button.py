#!/usr/bin/env python3
"""
Test script for issue #78: Login page Sign In button visible.

Issue: Sign In button is not visible on login page.

Fix: Fix CSS syntax error - missing ':root' selector prefix.
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "78")
HEADLESS = True


def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    return path


def test_login_button():
    """Test #78: Login page Sign In button is visible."""
    print("\n" + "=" * 50)
    print("Test #78: Login page Sign In button visible")
    print("=" * 50)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to login page
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")
            
            # Take screenshot of login page
            screenshot_path = take_screenshot(page, "01_login_page.png")
            print(f"  Screenshot: {screenshot_path}")
            
            # Check Sign In button exists and is visible
            login_btn = page.locator("#login-btn")
            expect(login_btn).to_be_visible()
            
            # Check button has correct text
            btn_text = login_btn.inner_text()
            assert "Sign In" in btn_text, f"Button text should contain 'Sign In', got: {btn_text}"
            print(f"  ✓ Sign In button is visible with text: '{btn_text}'")
            
            # Check button has background color (not transparent)
            btn_style = login_btn.evaluate("el => window.getComputedStyle(el).backgroundColor")
            print(f"  ✓ Button background color: {btn_style}")
            assert btn_style != "rgba(0, 0, 0, 0)", "Button should have a background color"
            
            print("  ✓ Test #78 PASSED")
            return True
            
        except Exception as e:
            print(f"  ✗ Test #78 FAILED: {e}")
            take_screenshot(page, "error_78.png")
            return False
        finally:
            browser.close()


def main():
    """Run test."""
    print("\n" + "=" * 60)
    print(f"Issue #78 Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    result = test_login_button()
    
    print("\n" + "=" * 60)
    print(f"Result: {'✓ PASSED' if result else '✗ FAILED'}")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)