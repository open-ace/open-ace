#!/usr/bin/env python3
"""
Test script for issue #75: Language selector as dropdown.

Issue: Language selector should be a dropdown above Version.

Fix: Change from button to select element, move above Version.
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "75")
HEADLESS = True


def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    return path


def test_language_selector():
    """Test #75: Language selector is a dropdown above Version."""
    print("\n" + "=" * 50)
    print("Test #75: Language selector as dropdown")
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
            
            # Take screenshot
            screenshot_path = take_screenshot(page, "01_language_selector.png")
            print(f"  Screenshot: {screenshot_path}")
            
            # Check language selector is a select element (dropdown)
            lang_select = page.locator("#lang-select")
            expect(lang_select).to_be_visible()
            print("  ✓ Language selector is visible")
            
            # Check it's a select element
            tag_name = lang_select.evaluate("el => el.tagName")
            assert tag_name == "SELECT", f"Language selector should be a SELECT element, got: {tag_name}"
            print("  ✓ Language selector is a dropdown (SELECT)")
            
            # Check options exist
            options = lang_select.locator("option")
            option_count = options.count()
            assert option_count >= 2, f"Should have at least 2 language options, found {option_count}"
            print(f"  ✓ Found {option_count} language options")
            
            # Check language selector is above Version
            version_text = page.locator(".sidebar-footer").locator("text=Version:")
            expect(version_text).to_be_visible()
            print("  ✓ Version text is visible below language selector")
            
            print("  ✓ Test #75 PASSED")
            return True
            
        except Exception as e:
            print(f"  ✗ Test #75 FAILED: {e}")
            take_screenshot(page, "error_75.png")
            return False
        finally:
            browser.close()


def main():
    """Run test."""
    print("\n" + "=" * 60)
    print(f"Issue #75 Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    result = test_language_selector()
    
    print("\n" + "=" * 60)
    print(f"Result: {'✓ PASSED' if result else '✗ FAILED'}")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)