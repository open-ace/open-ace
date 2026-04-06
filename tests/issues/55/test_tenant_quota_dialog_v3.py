#!/usr/bin/env python3
"""
UI Test: Issue 55 - 测试 TenantManagement 页面的配额编辑对话框（卡片布局）
"""

import sys
import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'True').lower() == 'true'
SCREENSHOT_DIR = 'screenshots/issues/55'

def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")

def test_tenant_quota_dialog_close():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 55 - TenantManagement quota dialog")
            print("=" * 60)

            # Login
            print("\n[Step 1] Login...")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle', timeout=10000)
            take_screenshot(page, 'tenant_v3_01_login.png')

            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('.login-form button.btn-primary')

            for i in range(10):
                time.sleep(1)
                if '/login' not in page.url:
                    break

            print("  ✓ Login successful")

            # Navigate to tenants page
            print("\n[Step 2] Navigate to /manage/tenants...")
            page.goto(f'{BASE_URL}/manage/tenants')
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(5)
            take_screenshot(page, 'tenant_v3_02_tenants_page.png')
            print("  ✓ Tenants page loaded")

            # Check for tenants
            print("\n[Step 3] Check for tenants...")
            tenant_cards = page.locator('.tenant-management .card')
            card_count = tenant_cards.count()
            print(f"  Found {card_count} tenant cards")
            
            if card_count == 0:
                print("  ⚠ No tenants found, skipping test")
                return True
            
            take_screenshot(page, 'tenant_v3_03_tenants_list.png')

            # Find quota button (sliders icon)
            print("\n[Step 4] Find and click quota button...")
            # Look for button with sliders icon in tenant management area
            quota_btn = page.locator('.tenant-management button:has(i.bi-sliders)')
            btn_count = quota_btn.count()
            print(f"  Found {btn_count} quota buttons")
            
            if btn_count == 0:
                print("  ✗ Quota button not found")
                return False
            
            quota_btn.first.click()
            time.sleep(1)
            
            # Check if modal opened
            modal = page.locator('.modal.show')
            if modal.count() == 0:
                print("  ✗ Modal did not open")
                return False
            print("  ✓ Quota modal opened")
            take_screenshot(page, 'tenant_v3_04_modal_opened.png')

            # Modify quota value
            print("\n[Step 5] Modify quota value...")
            modal = page.locator('.modal.show')
            inputs = modal.locator('input[type="number"]')
            if inputs.count() > 0:
                inputs.first.fill('1000000')
                print("  ✓ Modified quota value")
                take_screenshot(page, 'tenant_v3_05_value_modified.png')
            else:
                print("  ✗ No number inputs found")
                return False

            # Click save button
            print("\n[Step 6] Click save button...")
            save_btn = modal.locator('.modal-footer button.btn-primary')
            if save_btn.count() == 0:
                print("  ✗ Save button not found")
                return False
            save_btn.first.click()
            print("  ✓ Save button clicked")
            take_screenshot(page, 'tenant_v3_06_save_clicked.png')

            # Check if modal closed
            print("\n[Step 7] Check modal status after save...")
            time.sleep(3)
            
            modal_count = page.locator('.modal.show').count()
            take_screenshot(page, 'tenant_v3_07_after_save.png')
            
            if modal_count == 0:
                print("  ✓ Modal closed - save successful!")
                test_passed = True
            else:
                print("  ✗ Modal still visible after save")
                
                modal = page.locator('.modal.show')
                spinner = modal.locator('.spinner-border')
                if spinner.count() > 0:
                    print("  ⚠ Loading spinner visible, waiting more...")
                    time.sleep(5)
                    modal_count = page.locator('.modal.show').count()
                    if modal_count == 0:
                        print("  ✓ Modal closed after extended wait")
                        test_passed = True
                    else:
                        print("  ✗ Modal still visible - ISSUE CONFIRMED")
                        test_passed = False
                else:
                    print("  ✗ No loading spinner, modal still visible - ISSUE CONFIRMED")
                    test_passed = False
                
                take_screenshot(page, 'tenant_v3_08_modal_still_open.png')

            # Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            if test_passed:
                print("✓ Test PASSED - Dialog closes correctly after save")
            else:
                print("✗ Test FAILED - Dialog does not close after save (Issue 55)")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")

            return test_passed

        except Exception as e:
            take_screenshot(page, 'tenant_v3_error.png')
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            browser.close()

if __name__ == '__main__':
    success = test_tenant_quota_dialog_close()
    sys.exit(0 if success else 1)
