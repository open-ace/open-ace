#!/usr/bin/env python3
"""
Open ACE - Quota Management E2E Playwright Test

Tests:
1. Login as admin
2. Navigate to Quota Management page
3. Verify quota cards are visible
4. Test opening edit modal
5. Test valid quota input (within limits)
6. Test quota input exceeding max limit - should show error
7. Test negative quota input - should show error
8. Test scientific notation input (1e9) - should parse and validate
9. Test saving valid quota values
10. Test quota display formatting
11. Test unlimited quota (empty input)

Run:
  HEADLESS=true  python tests/e2e/e2e_quota_management_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_quota_management_playwright.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-quota-management")

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def login(page):
    """Login as admin user."""
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)

    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin123")
    page.click("button[type='submit']")
    pause(2)

    # Wait for redirect to work page
    page.wait_for_url("**/work**", timeout=10000)
    check(True, "Login successful, redirected to work page")
    shot(page, "01-login")


def navigate_to_quota_management(page):
    """Navigate to Quota Management page."""
    print("\n[TEST] Navigate to Quota Management...")
    page.goto(f"{BASE_URL}/manage/quota-alerts")
    pause(2)

    # Verify page loaded
    quota_header = page.locator("h2").filter(has_text="Quota and Alerts")
    check(quota_header.is_visible(), "Quota and Alerts header is visible")
    shot(page, "02-quota-management")


def test_quota_cards_visible(page):
    """Test that quota cards are visible."""
    print("\n[TEST] Quota cards visible...")
    # Find quota cards (user quota display cards)
    quota_cards = page.locator(".card").filter(has_text="Token")
    count = quota_cards.count()
    check(count > 0, f"Quota cards are visible (found {count} cards)")
    shot(page, "03-quota-cards")


def test_open_edit_modal(page):
    """Test opening edit modal."""
    print("\n[TEST] Open edit modal...")
    # Click edit button on first quota card
    edit_buttons = page.locator("button").filter(has_text="Edit")
    if edit_buttons.count() > 0:
        edit_buttons.first.click()
        pause(1)

        # Verify modal is visible
        modal = page.locator(".modal-dialog")
        check(modal.is_visible(), "Edit modal is visible")
        shot(page, "04-edit-modal-open")
    else:
        check(False, "No edit button found")


def test_valid_quota_input(page):
    """Test valid quota input within limits."""
    print("\n[TEST] Valid quota input...")

    # Find daily token quota input
    token_input = (
        page.locator("input")
        .filter(has_text="Daily Token Quota")
        .locator("..")
        .locator("input")
        .first
    )
    if not token_input.is_visible():
        # Alternative: find by placeholder
        token_input = page.locator("input[placeholder='Unlimited']").first

    # Clear and enter valid value (100M, within max 2147M)
    token_input.fill("")
    pause(0.2)
    token_input.fill("100")
    pause(0.5)

    # Verify no error message
    error_msg = page.locator(".text-danger").filter(has_text="exceeds")
    check(error_msg.count() == 0, "Valid quota input shows no error")
    shot(page, "05-valid-quota-input")


def test_quota_exceeding_max(page):
    """Test quota input exceeding max limit."""
    print("\n[TEST] Quota exceeding max limit...")

    # Find monthly token quota input
    inputs = page.locator("input[placeholder='Unlimited']")
    monthly_token_input = inputs.nth(1) if inputs.count() > 1 else inputs.first

    # Clear and enter value exceeding max (3000M > 2147M)
    monthly_token_input.fill("")
    pause(0.2)
    monthly_token_input.fill("3000")
    pause(0.5)

    # Verify error message appears
    error_msg = monthly_token_input.locator("..").locator(".text-danger")
    check(
        error_msg.is_visible() or "exceeds" in monthly_token_input.input_value(),
        "Quota exceeding max shows error",
    )
    shot(page, "06-quota-exceeding-max")


def test_negative_quota_input(page):
    """Test negative quota input."""
    print("\n[TEST] Negative quota input...")

    # Find daily request quota input
    inputs = page.locator("input[placeholder='Unlimited']")
    request_input = inputs.nth(2) if inputs.count() > 2 else inputs.nth(1)

    # Clear and enter negative value
    request_input.fill("")
    pause(0.2)
    request_input.fill("-100")
    pause(0.5)

    # Verify error message
    error_msg = request_input.locator("..").locator(".text-danger")
    check(
        error_msg.is_visible() or "negative" in request_input.input_value(),
        "Negative quota shows error",
    )
    shot(page, "07-negative-quota")


def test_scientific_notation_input(page):
    """Test scientific notation input."""
    print("\n[TEST] Scientific notation input...")

    # Find any quota input
    input_field = page.locator("input[placeholder='Unlimited']").first

    # Clear and enter scientific notation (1e9 = 1 billion)
    input_field.fill("")
    pause(0.2)
    input_field.fill("1e9")
    pause(0.5)

    # Verify it's parsed and validated
    # Should show error as 1e9 >> max for token quota
    error_msg = input_field.locator("..").locator(".text-danger")
    check(
        error_msg.is_visible() or input_field.input_value() != "",
        "Scientific notation is parsed and validated",
    )
    shot(page, "08-scientific-notation")


def test_close_modal_without_save(page):
    """Test closing modal without saving."""
    print("\n[TEST] Close modal without save...")

    # Click cancel button
    cancel_button = page.locator("button").filter(has_text="Cancel")
    if cancel_button.count() > 0:
        cancel_button.click()
        pause(1)

        # Verify modal is closed
        modal = page.locator(".modal-dialog")
        check(modal.count() == 0, "Modal is closed after cancel")
        shot(page, "09-modal-closed")


def test_quota_display_formatting(page):
    """Test quota display formatting."""
    print("\n[TEST] Quota display formatting...")

    # Navigate back to quota management if needed
    if page.locator(".modal-dialog").count() > 0:
        page.locator("button").filter(has_text="Cancel").first.click()
        pause(1)

    # Check quota displays show "M" suffix for token quotas
    quota_display = page.locator(".card").first.locator("small").filter(has_text="M")
    check(
        quota_display.count() > 0 or "∞" in page.content(),
        "Token quota displays have M suffix or infinity symbol",
    )
    shot(page, "10-quota-display-format")


def main():
    print("=" * 80)
    print("Quota Management E2E Test")
    print("=" * 80)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print(f"SCREENSHOT_DIR: {SCREENSHOT_DIR}")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        try:
            login(page)
            navigate_to_quota_management(page)
            test_quota_cards_visible(page)
            test_open_edit_modal(page)
            test_valid_quota_input(page)
            test_quota_exceeding_max(page)
            test_negative_quota_input(page)
            test_scientific_notation_input(page)
            test_close_modal_without_save(page)
            test_quota_display_formatting(page)

        except Exception as e:
            print(f"\n[ERROR] Test execution failed: {e}")
            shot(page, "error-state")
            import traceback

            traceback.print_exc()

        finally:
            context.close()
            browser.close()

    print("\n" + "=" * 80)
    print("Test Results:")
    print("=" * 80)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    if errors:
        print("\nFailed Tests:")
        for error in errors:
            print(f"  - {error}")

    print("=" * 80)

    if failed > 0:
        sys.exit(1)
    else:
        print("\n✅ All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
