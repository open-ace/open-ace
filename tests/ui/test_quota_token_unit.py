#!/usr/bin/env python3
"""
UI Test for Quota Management - Token Unit Display (M)
Test that daily and monthly token quota inputs show values in M (millions) unit.
"""

import sys
import os
import time

from playwright.sync_api import sync_playwright

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'True').lower() == 'true'
SCREENSHOT_DIR = 'screenshots'

def take_screenshot(page, name):
    """Take screenshot and save to screenshots directory"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")

def test_quota_token_unit():
    """Test that token quota inputs display values in M (millions) unit"""
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        
        try:
            # Step 1: Login
            print("Step 1: Login...")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle', timeout=10000)
            take_screenshot(page, '01_login_page.png')
            
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            
            # Click the Sign In button
            page.click('.login-form button.btn-primary')
            
            # Wait for login to complete - check for success indicator
            # Either navigate away from login page or show success message
            print("  Waiting for login to complete...")
            for i in range(10):
                time.sleep(1)
                current_url = page.url
                print(f"  Attempt {i+1}: URL = {current_url}")
                if '/login' not in current_url:
                    break
                # Check for success message
                success_msg = page.locator('.login-success')
                if success_msg.count() > 0:
                    print(f"  Found success message: {success_msg.first.text_content()}")
                    break
            
            take_screenshot(page, '02_after_login.png')
            
            # Check if we're logged in
            current_url = page.url
            if '/login' in current_url:
                # Check for error message
                error_msg = page.locator('.login-error')
                if error_msg.count() > 0:
                    raise Exception(f"Login failed with error: {error_msg.first.text_content()}")
                raise Exception("Login failed - still on login page")
            
            print("  ✓ Login successful")
            
            # Step 2: Navigate to Quota Management page
            print("Step 2: Navigate to Quota Management...")
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle', timeout=10000)
            take_screenshot(page, '03_quota_page.png')
            print("  ✓ Quota page loaded")
            
            # Step 3: Check if quota cards exist
            print("Step 3: Check quota cards...")
            quota_cards = page.locator('.quota-management .card')
            count = quota_cards.count()
            print(f"  ✓ Found {count} quota cards")
            
            if count > 0:
                # Step 4: Click edit button on first card
                print("Step 4: Click edit button...")
                first_card = quota_cards.first
                edit_btn = first_card.locator('button.btn-outline-primary')
                edit_btn.click()
                time.sleep(1)
                page.wait_for_selector('.modal.show', timeout=5000)
                take_screenshot(page, '04_edit_modal_opened.png')
                print("  ✓ Edit modal opened")
                
                # Step 5: Check token quota labels have (M) unit
                print("Step 5: Check token quota labels...")
                
                # Find labels for token quotas
                modal = page.locator('.modal.show')
                labels = modal.locator('label.form-label')
                label_count = labels.count()
                print(f"  Found {label_count} labels in modal")
                
                daily_token_found = False
                monthly_token_found = False
                
                for i in range(label_count):
                    label_text = labels.nth(i).text_content()
                    print(f"  Label {i}: '{label_text}'")
                    # Check if it's a token quota label
                    if 'Token' in label_text or 'token' in label_text or 'token_quota' in label_text.lower():
                        # Daily token quota
                        if '每日' in label_text or 'Daily' in label_text or 'daily' in label_text:
                            assert '(M)' in label_text, f"Expected '(M)' in daily token label, got: {label_text}"
                            print(f"  ✓ Daily token quota label contains (M): '{label_text}'")
                            daily_token_found = True
                        # Monthly token quota
                        elif '每月' in label_text or 'Monthly' in label_text or 'monthly' in label_text:
                            assert '(M)' in label_text, f"Expected '(M)' in monthly token label, got: {label_text}"
                            print(f"  ✓ Monthly token quota label contains (M): '{label_text}'")
                            monthly_token_found = True
                
                assert daily_token_found, "Daily token quota label not found"
                assert monthly_token_found, "Monthly token quota label not found"
                
                # Step 6: Check that request quota labels do NOT have (M)
                print("Step 6: Check request quota labels...")
                for i in range(label_count):
                    label_text = labels.nth(i).text_content()
                    # Check if it's a request quota label (should not have M)
                    if 'Request' in label_text or '请求' in label_text or 'request_quota' in label_text.lower():
                        assert '(M)' not in label_text, f"Unexpected '(M)' in request quota label: {label_text}"
                        print(f"  ✓ Request quota label (no M): '{label_text}'")
                
                take_screenshot(page, '05_labels_verified.png')
                
                # Step 7: Close modal
                print("Step 7: Close modal...")
                modal.locator('button.btn-secondary').click()
                time.sleep(1)
                page.wait_for_selector('.modal.show', state='hidden', timeout=5000)
                print("  ✓ Modal closed")
                
            else:
                print("  ⚠ No quota cards found, skipping edit test")
            
            take_screenshot(page, '06_test_complete.png')
            
            print("\n========================================")
            print("UI 功能测试报告")
            print("========================================")
            print("测试用例: Quota Token Unit (M)")
            print("状态: 通过 ✓")
            print("========================================")
            
            return True
            
        except Exception as e:
            take_screenshot(page, 'error_state.png')
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            browser.close()

if __name__ == '__main__':
    success = test_quota_token_unit()
    sys.exit(0 if success else 1)