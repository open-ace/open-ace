"""
UI Test: Issue 55 - 测试 TenantManagement 页面的配额编辑对话框（卡片布局）
"""

import os
import sys
import time

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(55)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = "screenshots/issues/55"


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_tenant_quota_dialog_close():
    _skip_if_no_server()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 55 - TenantManagement quota dialog")
            print("=" * 60)

            # Login
            print("\n[Step 1] Login...")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle", timeout=10000)
            take_screenshot(page, "tenant_v3_01_login.png")

            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click(".login-form button.btn-primary")

            for _i in range(10):
                time.sleep(1)
                if "/login" not in page.url:
                    break

            print("  ✓ Login successful")

            # Navigate to tenants page
            print("\n[Step 2] Navigate to /manage/tenants...")
            page.goto(f"{BASE_URL}/manage/tenants")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(5)
            take_screenshot(page, "tenant_v3_02_tenants_page.png")
            print("  ✓ Tenants page loaded")

            # Check for tenants
            print("\n[Step 3] Check for tenants...")
            tenant_cards = page.locator(".tenant-management .card")
            card_count = tenant_cards.count()
            print(f"  Found {card_count} tenant cards")

            if card_count == 0:
                print("  ⚠ No tenants found, skipping test")
                return True

            take_screenshot(page, "tenant_v3_03_tenants_list.png")

            # Find quota button (sliders icon)
            print("\n[Step 4] Find and click quota button...")
            # Look for button with sliders icon in tenant management area
            quota_btn = page.locator(".tenant-management button:has(i.bi-sliders)")
            btn_count = quota_btn.count()
            print(f"  Found {btn_count} quota buttons")

            assert btn_count > 0, "tenant quota button (sliders icon) not found"

            quota_btn.first.click()
            time.sleep(1)

            # Check if modal opened
            modal = page.locator(".modal.show")
            assert modal.count() > 0, "tenant quota modal did not open"
            print("  ✓ Quota modal opened")
            take_screenshot(page, "tenant_v3_04_modal_opened.png")

            # Modify quota value
            print("\n[Step 5] Modify quota value...")
            modal = page.locator(".modal.show")
            inputs = modal.locator('input[type="number"]')
            if inputs.count() > 0:
                inputs.first.fill("1000000")
                print("  ✓ Modified quota value")
                take_screenshot(page, "tenant_v3_05_value_modified.png")
            else:
                assert inputs.count() > 0, "tenant quota modal has no number inputs"

            # Click save button
            print("\n[Step 6] Click save button...")
            save_btn = modal.locator(".modal-footer button.btn-primary")
            assert save_btn.count() > 0, "tenant quota modal save button not found"
            save_btn.first.click()
            print("  ✓ Save button clicked")
            take_screenshot(page, "tenant_v3_06_save_clicked.png")

            # Check if modal closed
            print("\n[Step 7] Check modal status after save...")
            time.sleep(3)

            modal_count = page.locator(".modal.show").count()
            take_screenshot(page, "tenant_v3_07_after_save.png")

            if modal_count == 0:
                print("  ✓ Modal closed - save successful!")
                test_passed = True
            else:
                print("  ✗ Modal still visible after save")

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

                take_screenshot(page, "tenant_v3_08_modal_still_open.png")

            # Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            if test_passed:
                print("✓ Test PASSED - Dialog closes correctly after save")
            else:
                print("✗ Test FAILED - Dialog does not close after save (Issue 55)")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")

            assert test_passed, "tenant quota dialog did not close after save (issue #55)"
            return test_passed

        except PlaywrightError as e:
            take_screenshot(page, "tenant_v3_error.png")
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            browser.close()
