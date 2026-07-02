#!/usr/bin/env python3
"""
Open ACE - Model Gateway Config E2E Playwright Test

Admin-only page at /manage/settings/model-gateway (feature #720).
Success signals intentionally avoid the toast container (which is not mounted
app-wide): Save success is inferred from the Delete button appearing (rendered
only when a config row exists) and from base URL repopulating after reload.

Tests:
1. Login as admin -> page loads with expected fields
2. Save persists config (Delete button appears; reload repopulates base URL)
3. Model Prefix Mode toggle reveals/hides the prefix input
4. Test Connection button enables only when a base URL is present

Run:
  HEADLESS=true  python tests/e2e/e2e_model_gateway_playwright.py
  HEADLESS=false python tests/e2e/e2e_model_gateway_playwright.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-model-gateway")
GATEWAY_URL = f"{BASE_URL}/manage/settings/model-gateway"
BASEURL_PLACEHOLDER = "https://litellm.example.com/v1"

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=True)


def pause(seconds):
    time.sleep(seconds if not HEADLESS else 0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def text_inputs(page):
    """Non-checkbox, non-password text inputs (base URL, model prefix)."""
    return page.locator("input:not([type='checkbox']):not([type='password'])")


def login(page):
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    pause(2)
    page.wait_for_url("**/manage/**", timeout=10000)
    check(True, "Login successful")
    shot(page, "01-login")


def test_page_loads(page):
    print("\n[TEST] Model Gateway page loads...")
    page.goto(GATEWAY_URL)
    pause(2)
    header = page.locator("h2").filter(has_text="Model Gateway Configuration")
    check(header.is_visible(), "Model Gateway Configuration header is visible")
    base_url_input = page.locator(f"input[placeholder='{BASEURL_PLACEHOLDER}']")
    check(base_url_input.first.is_visible(), "Gateway Base URL input is visible")
    check(
        page.locator("input[type='password']").first.is_visible(),
        "Gateway API Key input is visible",
    )
    shot(page, "02-page-load")


def _delete_existing_via_api(page):
    """Remove any leftover config so the test starts clean (avoids confirm-dialog selectors)."""
    page.evaluate(
        "fetch('/api/management/model-gateway-config', {method:'DELETE', credentials:'include'})"
    )
    pause(0.5)
    page.goto(GATEWAY_URL)
    pause(1)


def test_save_persists(page):
    print("\n[TEST] Save persists config...")
    if page.locator("button").filter(has_text="Delete").count() > 0:
        _delete_existing_via_api(page)

    page.locator(f"input[placeholder='{BASEURL_PLACEHOLDER}']").first.fill(BASEURL_PLACEHOLDER)
    page.locator("input[type='password']").first.fill("sk-test-e2e-key")
    pause(0.3)
    page.locator("button").filter(has_text="Save").first.click()
    pause(2)

    delete_btn = page.locator("button").filter(has_text="Delete")
    check(delete_btn.count() > 0, "Delete button appears after save (config persisted)")
    shot(page, "03-after-save")

    page.reload()
    pause(2)
    reloaded = page.locator(f"input[placeholder='{BASEURL_PLACEHOLDER}']").first.input_value()
    check(reloaded == BASEURL_PLACEHOLDER, "Base URL repopulates after reload")
    shot(page, "04-after-reload")


def test_prefix_toggle(page):
    print("\n[TEST] Model Prefix Mode toggle...")
    checkbox = page.locator("input[type='checkbox']").first
    before = text_inputs(page).count()
    checkbox.click()
    pause(0.5)
    after = text_inputs(page).count()
    check(after > before, "Prefix input appears when Prefix Mode toggled on")
    shot(page, "05-prefix-on")

    checkbox.click()
    pause(0.5)
    off = text_inputs(page).count()
    check(off < after, "Prefix input hides when Prefix Mode toggled off")
    shot(page, "06-prefix-off")


def test_connection_button_state(page):
    print("\n[TEST] Test Connection button enables only with base URL...")
    base_url_input = page.locator(f"input[placeholder='{BASEURL_PLACEHOLDER}']").first
    test_btn = page.locator("button").filter(has_text="Test Connection").first
    base_url_input.fill("")
    pause(0.3)
    check(test_btn.is_disabled(), "Test Connection disabled when base URL empty")
    base_url_input.fill(BASEURL_PLACEHOLDER)
    pause(0.3)
    check(test_btn.is_enabled(), "Test Connection enabled when base URL filled")
    shot(page, "07-test-button-state")


def main():
    print("=" * 80)
    print("Model Gateway Config E2E Test (#720)")
    print("=" * 80)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        try:
            login(page)
            test_page_loads(page)
            test_save_persists(page)
            test_prefix_toggle(page)
            test_connection_button_state(page)
        except Exception as e:  # noqa: BLE001
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
        for err in errors:
            print(f"  - {err}")
    print("=" * 80)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
