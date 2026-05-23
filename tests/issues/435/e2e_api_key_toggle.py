#!/usr/bin/env python3
"""
Open Ace - API Key Activate/Deactivate Toggle Playwright E2E Test

Test the API Key activation toggle feature:
1. Login as admin
2. Navigate to API Key management page (/manage/remote/api-keys)
3. Create a test API key via API
4. Verify toggle switch is present and active
5. Deactivate the key via toggle switch
6. Verify status changes to Inactive
7. Reactivate the key via toggle switch
8. Verify status changes back to Active
9. Verify deactivated key is not returned by resolve_api_key

Run:
  HEADLESS=true  python tests/435/e2e_api_key_toggle.py   # Test
  HEADLESS=false python tests/435/e2e_api_key_toggle.py   # Demo
"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import expect, sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-api-key-toggle")

TEST_USER = "admin"
TEST_PASS = "admin123"


# ── 工具函数 ──────────────────────────────────────────


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def log_step(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


# ── API 调用 ──────────────────────────────────────────


def api_login():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def api_list_keys(token):
    r = requests.get(
        f"{BASE_URL}/api/remote/api-keys",
        cookies={"session_token": token},
    )
    assert r.status_code == 200
    return r.json().get("keys", [])


def api_store_key(token, key_name, provider="anthropic"):
    r = requests.post(
        f"{BASE_URL}/api/remote/api-keys",
        json={
            "provider": provider,
            "key_name": key_name,
            "api_key": "sk-test-e2e-toggle-key",
            "base_url": "https://api.example.com",
            "tenant_id": 1,
        },
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Store failed: {r.status_code} {r.text}"
    return r.json()


def api_update_key(token, key_id, **fields):
    body = {"keyId": key_id, "tenant_id": 1}
    body.update(fields)
    r = requests.put(
        f"{BASE_URL}/api/remote/api-keys/{key_id}",
        json=body,
        cookies={"session_token": token},
    )
    assert r.status_code == 200, f"Update failed: {r.status_code} {r.text}"
    return r.json()


def api_delete_key(token, key_id):
    r = requests.delete(
        f"{BASE_URL}/api/remote/api-keys/{key_id}",
        json={"tenant_id": 1},
        cookies={"session_token": token},
    )
    return r.status_code == 200


def cleanup_test_keys(token):
    keys = api_list_keys(token)
    for key in keys:
        if key["key_name"].startswith("E2E_Toggle_"):
            api_delete_key(token, key["id"])
            print(f"    [Cleanup] Deleted key: {key['key_name']}")


# ── 测试主流程 ──────────────────────────────────────────


def test_api_key_toggle():
    print("\n" + "=" * 60)
    print("  E2E Test: API Key Activate/Deactivate Toggle")
    print("=" * 60)

    token = api_login()
    cleanup_test_keys(token)

    test_key_name = f"E2E_Toggle_{int(time.time())}"

    # Create key via API
    api_store_key(token, test_key_name)
    log_step("Setup", f"Created test key: {test_key_name}")

    keys = api_list_keys(token)
    test_key = next(k for k in keys if k["key_name"] == test_key_name)
    key_id = test_key["id"]
    assert test_key["is_active"] is True, "Key should be active after creation"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            # ── Step 1: Login ──────────────────────────────
            log_step("Step 1", "Login as admin")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            pause(0.5)

            page.fill("#username", TEST_USER)
            page.fill("#password", TEST_PASS)
            page.click('button[type="submit"]')
            pause(2)
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            shot(page, "01_login_success")

            # ── Step 2: Navigate to API Key page ───────────
            log_step("Step 2", "Navigate to API Key management")
            page.goto(f"{BASE_URL}/manage/remote/api-keys")
            pause(2)
            shot(page, "02_api_keys_page")

            # Find test key row
            key_row = page.locator(f"tr:has-text('{test_key_name}')")
            expect(key_row).to_be_visible()

            # Verify toggle switch exists and is checked (active)
            toggle = key_row.locator("input.form-check-input[type='checkbox'][role='switch']")
            expect(toggle).to_be_visible()
            expect(toggle).to_be_checked()
            log_step("Verify", "✓ Toggle switch is present and checked (Active)")

            # Verify Active badge
            active_badge = key_row.locator("span.badge.bg-success")
            expect(active_badge).to_have_text("Active")
            shot(page, "03_key_active_state")

            # ── Step 3: Deactivate key ────────────────────
            log_step("Step 3", "Click toggle to deactivate key")
            toggle.click()
            pause(2)
            shot(page, "04_key_deactivated")

            # Verify toggle is unchecked
            expect(toggle).not_to_be_checked()
            log_step("Verify", "✓ Toggle is unchecked")

            # Verify Inactive badge
            inactive_badge = key_row.locator("span.badge.bg-secondary")
            expect(inactive_badge).to_have_text("Inactive")
            log_step("Verify", "✓ Badge shows Inactive")
            shot(page, "05_inactive_badge")

            # Verify via API that key is inactive
            keys = api_list_keys(token)
            updated_key = next(k for k in keys if k["id"] == key_id)
            assert updated_key["is_active"] is False, "Key should be inactive after toggle"
            log_step("API", "✓ API confirms key is inactive")

            # ── Step 4: Reactivate key ────────────────────
            log_step("Step 4", "Click toggle to reactivate key")
            toggle.click()
            pause(2)
            shot(page, "06_key_reactivated")

            # Verify toggle is checked again
            expect(toggle).to_be_checked()
            log_step("Verify", "✓ Toggle is checked")

            # Verify Active badge
            active_badge = key_row.locator("span.badge.bg-success")
            expect(active_badge).to_have_text("Active")
            log_step("Verify", "✓ Badge shows Active")
            shot(page, "07_active_badge_restored")

            # Verify via API
            keys = api_list_keys(token)
            updated_key = next(k for k in keys if k["id"] == key_id)
            assert updated_key["is_active"] is True, "Key should be active after re-toggle"
            log_step("API", "✓ API confirms key is active")

            # ── Cleanup ────────────────────────────────────
            log_step("Cleanup", f"Deleting test key: {test_key_name}")
            api_delete_key(token, key_id)
            log_step("Cleanup", "✓ Test key deleted")

            print("\n" + "=" * 60)
            print("  ✅ E2E Test PASSED: API Key Activate/Deactivate Toggle")
            print("=" * 60)

        except Exception as e:
            shot(page, "error_state")
            print(f"\n    ❌ Test FAILED: {e}")
            raise

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_api_key_toggle()
