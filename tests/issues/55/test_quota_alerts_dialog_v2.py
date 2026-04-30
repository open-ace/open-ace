#!/usr/bin/env python3
"""
UI Test: Issue 55 - 测试 QuotaAlerts 页面的配额编辑对话框

测试内容：
1. 登录系统
2. 导航到 /manage/quota 页面
3. 点击编辑配额按钮打开对话框
4. 修改配额值
5. 点击保存按钮
6. 验证对话框是否关闭
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "True").lower() == "true"
SCREENSHOT_DIR = "screenshots/issues/55"


def take_screenshot(page, name):
    """Take screenshot and save to screenshots directory"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")


def test_quota_alerts_dialog_close():
    """Test quota alerts page dialog closes after clicking save button"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 55 - QuotaAlerts dialog close after save")
            print("=" * 60)

            # Step 1: Login
            print("\n[Step 1] Login...")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle", timeout=10000)
            take_screenshot(page, "alerts_v2_01_login.png")

            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click(".login-form button.btn-primary")

            for i in range(10):
                time.sleep(1)
                if "/login" not in page.url:
                    break

            take_screenshot(page, "alerts_v2_02_after_login.png")
            print("  ✓ Login successful")

            # Step 2: Navigate to /manage/quota page
            print("\n[Step 2] Navigate to /manage/quota page...")
            page.goto(f"{BASE_URL}/manage/quota")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(5)  # Wait for API data to load
            take_screenshot(page, "alerts_v2_03_quota_page.png")
            print("  ✓ Quota page loaded")

            # Verify QuotaAlerts component is rendered
            quota_alerts = page.locator(".quota-alerts")
            if quota_alerts.count() == 0:
                print("  ✗ QuotaAlerts component not found")
                return False
            print("  ✓ QuotaAlerts component found")

            # Step 3: Check quota cards (in .row.g-3)
            print("\n[Step 3] Check quota cards...")
            cards = page.locator(".row.g-3 .card")
            count = cards.count()
            print(f"  Found {count} quota cards")

            if count == 0:
                print("  ✗ No quota cards found")
                return False
            take_screenshot(page, "alerts_v2_04_cards.png")

            # Step 4: Click edit button on first card
            print("\n[Step 4] Click edit button...")
            edit_btn = page.locator("button.btn-outline-primary:has(i.bi-pencil)").first
            edit_btn.click()
            time.sleep(1)

            # Check if modal opened
            modal = page.locator(".modal.show")
            if modal.count() == 0:
                print("  ✗ Modal did not open")
                return False
            print("  ✓ Edit modal opened")
            take_screenshot(page, "alerts_v2_05_modal_opened.png")

            # Step 5: Modify quota value
            print("\n[Step 5] Modify quota value...")
            modal = page.locator(".modal.show")
            inputs = modal.locator('input[type="number"]')
            if inputs.count() > 0:
                inputs.first.fill("100")
                print("  ✓ Modified quota value to 100")
                take_screenshot(page, "alerts_v2_06_value_modified.png")
            else:
                print("  ✗ No number inputs found")
                return False

            # Step 6: Click save button
            print("\n[Step 6] Click save button...")
            save_btn = modal.locator(".modal-footer button.btn-primary")
            if save_btn.count() == 0:
                print("  ✗ Save button not found")
                return False
            save_btn.first.click()
            print("  ✓ Save button clicked")
            take_screenshot(page, "alerts_v2_07_save_clicked.png")

            # Step 7: Wait and check if modal closed
            print("\n[Step 7] Check modal status after save...")
            time.sleep(3)

            modal_count = page.locator(".modal.show").count()
            take_screenshot(page, "alerts_v2_08_after_save.png")

            if modal_count == 0:
                print("  ✓ Modal closed - save successful!")
                test_passed = True
            else:
                print("  ✗ Modal still visible after save")

                # Check for loading spinner
                modal = page.locator(".modal.show")
                spinner = modal.locator(".spinner-border")
                if spinner.count() > 0:
                    print("  ⚠ Loading spinner visible, waiting more...")
                    time.sleep(5)
                    modal_count = page.locator(".modal.show").count()
                    if modal_count == 0:
                        print("  ✓ Modal closed after extended wait")
                        test_passed = True
                    else:
                        print("  ✗ Modal still visible - ISSUE CONFIRMED")
                        test_passed = False
                else:
                    print("  ✗ No loading spinner, modal still visible - ISSUE CONFIRMED")
                    test_passed = False

                take_screenshot(page, "alerts_v2_09_modal_still_open.png")

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
            take_screenshot(page, "alerts_v2_error.png")
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            browser.close()


if __name__ == "__main__":
    success = test_quota_alerts_dialog_close()
    sys.exit(0 if success else 1)
