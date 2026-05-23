#!/usr/bin/env python3
"""
Open ACE - API Key CLI Settings Configuration Playwright E2E Test

Test the API Key management with CLI Settings configuration:
1. Login as admin
2. Navigate to API Key management page (/manage/remote/api-keys)
3. Add a new API key with CLI settings (claude-code, qwen-code)
4. Verify the key appears in table with CLI tools badges
5. Edit the API key to update CLI settings
6. Verify settings are persisted

Run:
  HEADLESS=true  python tests/e2e_api_key_cli_settings.py   # Test
  HEADLESS=false python tests/e2e_api_key_cli_settings.py   # Demo
"""

import json
import os
import sys
import time

# Add project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import expect, sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-api-key-cli-settings")

# Test user (admin)
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
    """演示模式下慢放，headless 模式下快速通过"""
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


def api_delete_key(token, key_id):
    r = requests.delete(
        f"{BASE_URL}/api/remote/api-keys/{key_id}",
        json={"tenant_id": 1},
        cookies={"session_token": token},
    )
    return r.status_code == 200


def cleanup_test_keys(token):
    """Delete any test keys created during previous test runs."""
    keys = api_list_keys(token)
    for key in keys:
        if key["key_name"].startswith("E2E_Test_"):
            api_delete_key(token, key["id"])
            print(f"    [Cleanup] Deleted key: {key['key_name']}")


# ── 测试主流程 ──────────────────────────────────────────


def test_api_key_cli_settings():
    """Test API Key CLI Settings Configuration."""

    print("\n" + "=" * 60)
    print("  E2E Test: API Key CLI Settings Configuration")
    print("=" * 60)

    # Login first to get token for cleanup
    token = api_login()
    cleanup_test_keys(token)

    with sync_playwright() as p:
        # Launch browser with taller viewport
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 1200})
        page = context.new_page()

        try:
            # ── Step 1: Login ──────────────────────────────
            log_step("Step 1", "Login as admin")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            pause(0.5)

            # Fill login form
            page.fill("#username", TEST_USER)
            page.fill("#password", TEST_PASS)
            page.click('button[type="submit"]')
            pause(2)

            # Wait for redirect away from login page
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            shot(page, "01_login_success")
            log_step("Login", "✓ Successfully logged in")

            # ── Step 2: Navigate to API Key management ───────────
            log_step("Step 2", "Navigate to API Key management")
            page.goto(f"{BASE_URL}/manage/remote/api-keys")
            pause(2)
            shot(page, "02_api_keys_page")
            log_step("Nav", "✓ API Key management page loaded")

            # ── Step 3: Add new API key with CLI settings ──────────
            log_step("Step 3", "Click Add API Key button")
            page.click("button:has-text('Add API Key')")
            pause(1)
            shot(page, "03_add_dialog_open")

            # Fill form
            test_key_name = f"E2E_Test_{int(time.time())}"
            log_step("Form", f"Key name: {test_key_name}")

            # Select provider (Anthropic) - use .form-select class
            page.select_option(".form-select", value="anthropic")
            pause(0.5)

            # Fill key name - exact placeholder match
            page.fill("input[placeholder='Enter key name']", test_key_name)
            pause(0.5)

            # Fill API key (fake key for testing)
            page.fill("input[placeholder='Enter API key']", "sk-ant-test-e2e-key-12345")
            pause(0.5)

            # Fill base URL - exact placeholder match with (optional)
            page.fill(
                "input[placeholder='Enter base URL (optional)']", "https://api.z.ai/api/anthropic"
            )
            pause(0.5)

            # ── Step 4: Select CLI Tools ──────────────────────
            log_step("Step 4", "Wait for modal to fully render and select CLI tools")

            # Wait for modal content to be fully loaded
            modal = page.locator(".modal.show")
            modal.wait_for(state="visible", timeout=5000)

            # Scroll modal body to ensure all content is visible
            modal_body = modal.locator(".modal-body")
            modal_body.evaluate("el => el.scrollTop = el.scrollHeight")
            pause(1)

            shot(page, "04_modal_scrolled")

            log_step("Select", "Checking Claude Code and Qwen Code checkboxes")

            # Check Claude Code - wait for textarea to appear before checking Qwen Code
            page.evaluate(
                """() => {
                const modal = document.querySelector('.modal.show');
                const checkboxes = modal.querySelectorAll('input[type="checkbox"]');
                if (checkboxes[0]) checkboxes[0].click();
            }"""
            )
            # Wait for Claude Code textarea to render
            page.wait_for_selector("textarea.form-control", state="visible", timeout=5000)
            pause(1)

            # Now check Qwen Code
            page.evaluate(
                """() => {
                const modal = document.querySelector('.modal.show');
                const checkboxes = modal.querySelectorAll('input[type="checkbox"]');
                if (checkboxes[1]) checkboxes[1].click();
            }"""
            )
            pause(1)

            shot(page, "04_cli_tools_selected")

            # ── Step 5: Edit CLI Settings JSON ───────────────
            log_step("Step 5", "Verify JSON editors appear for selected tools")

            # Wait for both textareas to appear
            settings_textareas = page.locator("textarea.form-control")
            expect(settings_textareas).to_have_count(2, timeout=15000)

            # Claude Code settings textarea (first)
            claude_settings = settings_textareas.first
            claude_settings.fill(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_MODEL": "glm-5",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                        },
                        "model": "haiku",
                    },
                    indent=2,
                )
            )
            pause(0.5)

            # Qwen Code settings textarea (second)
            qwen_settings = settings_textareas.nth(1)
            qwen_settings.fill(
                json.dumps(
                    {
                        "modelProviders": {
                            "openai": [
                                {
                                    "id": "glm-5",
                                    "name": "[z.ai] glm-5",
                                    "envKey": "ZAI_API_KEY",
                                    "generationConfig": {"extra_body": {"enable_thinking": True}},
                                }
                            ]
                        },
                        "model": {"name": "glm-5"},
                    },
                    indent=2,
                )
            )
            pause(1)

            shot(page, "05_cli_settings_filled")
            log_step("Settings", "✓ CLI settings JSON filled")

            # ── Step 6: Save API Key ────────────────────────
            log_step("Step 6", "Click Save button")
            page.click("button:has-text('Save')")
            pause(2)

            # Wait for dialog to close (modal should disappear)
            page.wait_for_selector(".modal", state="hidden", timeout=5000)
            shot(page, "06_key_saved")
            log_step("Save", "✓ API Key saved")

            # ── Step 7: Verify key in table ───────────────────
            log_step("Step 7", "Verify new key appears in table with CLI tools")

            # Refresh page to ensure data is loaded
            page.reload()
            pause(2)

            # Find the test key row
            key_row = page.locator(f"tr:has-text('{test_key_name}')")
            expect(key_row).to_be_visible()

            # Verify CLI tools badges are shown
            claude_badge = key_row.locator("span.badge:has-text('claude-code')")
            expect(claude_badge).to_be_visible()

            qwen_badge = key_row.locator("span.badge:has-text('qwen-code')")
            expect(qwen_badge).to_be_visible()

            shot(page, "07_key_in_table")
            log_step("Verify", "✓ Key appears in table with CLI tool badges")

            # ── Step 8: Edit API Key ──────────────────────────
            log_step("Step 8", "Click Edit button to update CLI settings")

            # Click edit button (pencil icon) in the test key row
            edit_btn = key_row.locator("button:has(i.bi-pencil)")
            edit_btn.click()
            pause(1)

            shot(page, "08_edit_dialog_open")

            # Wait for edit modal to appear
            edit_modal = page.locator(".modal.show")
            edit_modal.wait_for(state="visible", timeout=5000)

            # Scroll modal body to show CLI Tools
            edit_modal_body = edit_modal.locator(".modal-body")
            edit_modal_body.evaluate("el => el.scrollTop = el.scrollHeight")
            pause(1)

            # Verify CLI tools checkboxes are checked inside modal
            edit_checkboxes = edit_modal_body.locator("input.form-check-input[type='checkbox']")
            expect(edit_checkboxes.first).to_be_checked()
            expect(edit_checkboxes.nth(1)).to_be_checked()

            # Verify settings are preserved
            settings_textareas = edit_modal_body.locator("textarea.form-control")
            expect(settings_textareas).to_have_count(2)

            # Update Claude settings
            claude_settings = settings_textareas.first
            claude_settings.fill(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_MODEL": "glm-5.1",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
                        },
                        "model": "sonnet",
                    },
                    indent=2,
                )
            )
            pause(0.5)

            shot(page, "09_settings_updated")

            # Save edited key
            page.click("button:has-text('Save')")
            pause(2)

            page.wait_for_selector(".modal", state="hidden", timeout=5000)
            shot(page, "10_edit_saved")
            log_step("Edit", "✓ API Key updated")

            # ── Step 9: Final verification ───────────────────
            log_step("Step 9", "Final verification - reload and check")

            page.reload()
            pause(2)

            # Verify key still exists
            key_row = page.locator(f"tr:has-text('{test_key_name}')")
            expect(key_row).to_be_visible()
            expect(key_row.locator("span.badge:has-text('claude-code')")).to_be_visible()
            expect(key_row.locator("span.badge:has-text('qwen-code')")).to_be_visible()

            shot(page, "11_final_verification")
            log_step("Final", "✓ All verifications passed")

            # ── Cleanup ─────────────────────────────────────
            log_step("Cleanup", f"Deleting test key: {test_key_name}")

            # Delete via API
            keys = api_list_keys(token)
            for key in keys:
                if key["key_name"] == test_key_name:
                    api_delete_key(token, key["id"])
                    log_step("Cleanup", "✓ Test key deleted")
                    break

            print("\n" + "=" * 60)
            print("  ✅ E2E Test PASSED: API Key CLI Settings Configuration")
            print("=" * 60)

        except Exception as e:
            shot(page, "error_state")
            print(f"\n    ❌ Test FAILED: {e}")
            raise

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_api_key_cli_settings()
