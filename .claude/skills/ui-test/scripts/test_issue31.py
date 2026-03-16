#!/usr/bin/env python3
"""Test script for Issue 31: Messages page should show remote machines."""

import sys
import os

# Add skill scripts to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui_test import UITester, DEFAULT_URL, DEFAULT_USERNAME, DEFAULT_PASSWORD

def test_issue31_remote_machines():
    """Test that Messages page shows remote machines in the host filter."""
    
    tester = UITester(
        url=DEFAULT_URL,
        username=DEFAULT_USERNAME,
        password=DEFAULT_PASSWORD,
        headless=False,
        output_dir="./screenshots"
    )
    
    try:
        # Start browser
        tester.start()
        
        # Step 1: Login
        tester.login()
        tester.screenshot("issue31_01_login.png")
        
        # Step 2: Navigate to Messages page
        tester.navigate("#nav-messages")
        tester.wait(2)
        tester.screenshot("issue31_02_messages_page.png")
        
        # Step 3: Check host filter exists
        host_filter_visible = tester.check_visible("#host-filter")
        if not host_filter_visible:
            print("FAIL: Host filter not found on Messages page")
            return False
        
        # Step 4: Get all options in host filter
        options = tester.page.locator("#host-filter option").all()
        option_texts = [opt.inner_text() for opt in options]
        
        print(f"Host filter options: {option_texts}")
        
        # Step 5: Check if remote machine 'ai-lab' is in the list
        has_ai_lab = any("ai-lab" in text for text in option_texts)
        
        if has_ai_lab:
            print("PASS: Remote machine 'ai-lab' is visible in Messages page host filter")
            tester.screenshot("issue31_03_host_filter_with_ai_lab.png")
            return True
        else:
            print("FAIL: Remote machine 'ai-lab' is NOT visible in Messages page host filter")
            tester.screenshot("issue31_03_host_filter_no_ai_lab.png")
            return False
            
    except Exception as e:
        print(f"ERROR: {e}")
        tester.screenshot("issue31_error.png")
        return False
    finally:
        tester.stop()


if __name__ == "__main__":
    success = test_issue31_remote_machines()
    sys.exit(0 if success else 1)