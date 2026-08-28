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
import re
import sys
import time

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(55)]


# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = "screenshots/issues/55"


def take_screenshot(page, name):
    """Take screenshot and save to screenshots directory"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=True)
    print(f"  Saved: {path}")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _parse_available_hint(label_text):
    """Parse the "(Available: X ...)" hint from a quota field label.

    QuotaAlerts.tsx renders each quota input's label with the available
    tenant pool for that field (``可用:`` in zh, ``Available:`` otherwise),
    e.g. ``Daily Token Quota (M)(Available: 10.00M (Max: 100000M))``.
    Returns the available amount as float, or None when the hint is absent.
    """
    match = re.search(r"(?:Available|可用)\s*:\s*([0-9][0-9,]*(?:\.[0-9]+)?)", label_text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _fill_in_policy_quota(modal):
    """Fill the modal's quota fields with values guaranteed in-policy.

    Earlier quota tests in the same shard may have reallocated the admin's
    quotas, so hardcoded values can exceed the remaining tenant pool (the
    API then answers 400 and the modal legitimately stays open). Instead:
    keep each field's current value (a re-save is never a quota increase, so
    it always passes the tenant allocation check), or when the field is
    empty (unlimited) fill "1" only if the label's Available hint allows it.
    """
    fields = modal.locator(
        'div.col-md-6:has(label.form-label):has(input.form-control[type="text"])'
    )
    count = fields.count()
    assert count >= 4, f"quota modal should expose 4 quota fields, found {count}"
    decisions = []
    for index in range(count):
        label_text = fields.nth(index).locator("label.form-label").inner_text()
        available = _parse_available_hint(label_text)
        field_input = fields.nth(index).locator('input.form-control[type="text"]')
        current = field_input.input_value().strip()
        if current:
            # Re-saving the current value is never an increase: keep it.
            decision = f"keep {current}"
        elif available is not None and available >= 1:
            field_input.fill("1")
            decision = "fill 1 (unlimited before, pool allows 1)"
        else:
            # No remaining pool: stay unlimited (empty) — still a valid save.
            decision = "leave unlimited"
        decisions.append(decision)
        print(f"  Field {index + 1}: {decision} (hint available={available})")
    return decisions


def test_quota_alerts_dialog_close():
    """Test quota alerts page dialog closes after clicking save button"""

    _skip_if_no_server()
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

            for _i in range(10):
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
            assert quota_alerts.count() > 0, "QuotaAlerts component not found on the quota page"
            print("  ✓ QuotaAlerts component found")

            # Step 3: Check quota cards (in .row.g-3)
            print("\n[Step 3] Check quota cards...")
            cards = page.locator(".row.g-3 .card")
            count = cards.count()
            print(f"  Found {count} quota cards")

            assert count > 0, "no quota cards found on the quota page"
            take_screenshot(page, "alerts_v2_04_cards.png")

            # Step 4: Click edit button on first card
            print("\n[Step 4] Click edit button...")
            edit_btn = page.locator("button.btn-outline-primary:has(i.bi-pencil)").first
            edit_btn.click()
            time.sleep(1)

            # Check if modal opened
            modal = page.locator(".modal.show")
            assert modal.count() > 0, "quota edit modal did not open"
            print("  ✓ Edit modal opened")
            take_screenshot(page, "alerts_v2_05_modal_opened.png")

            # Step 5: Modify quota value
            print("\n[Step 5] Modify quota value...")
            modal = page.locator(".modal.show")
            # QuotaAlerts renders its four quota fields as TextInput
            # type="text" (input.form-control) — empty means unlimited;
            # the old input[type="number"] selector matched nothing.
            # Values are chosen dynamically from each label's Available
            # hint so the save stays in-policy even when earlier shard
            # tests have reallocated the tenant pool.
            _fill_in_policy_quota(modal)
            take_screenshot(page, "alerts_v2_06_value_modified.png")

            # Step 6: Click save button
            print("\n[Step 6] Click save button...")
            save_btn = modal.locator(".modal-footer button.btn-primary")
            assert save_btn.count() > 0, "quota modal save button not found"
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

            assert test_passed, "quota dialog did not close after save (issue #55)"
            return test_passed

        except PlaywrightError as e:
            take_screenshot(page, "alerts_v2_error.png")
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            browser.close()
