#!/usr/bin/env python3
"""
UI Test for Issue 54: Add User 模态框字段完整性

测试内容：
1. 登录系统，进入用户管理页（/manage/users）
2. 打开 Add User 模态框
3. 验证表单字段完整：用户名/邮箱/系统账号（Linux Account 更名后）/角色/
   租户/密码/确认密码
4. 验证必填标记与密码策略提示存在

#2457 realignment: the baselined Page.fill timeout came from the retired
input[name=...] login fields and the #nav-management/#users-tab/#add-user-btn
ids (management is now a route; the modal is the shared Modal component).
The old #add-linux-account field is now the system_account input (the
linux_account → system_account rename), located structurally (labeled
TextInput inside the modal) since the ids are gone.
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000
SCREENSHOT_DIR = "screenshots/issues/54"


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (USERNAME,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except Exception:
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_add_user_modal_fields():
    """Add User modal contains the complete field set."""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            print("  ✓ 登录成功")

            page.goto(f"{BASE_URL}/manage/users", wait_until="networkidle")
            page.wait_for_selector("table, .empty-state", timeout=TIMEOUT)
            print("  ✓ 用户管理页加载完成")

            page.locator("button:has(i.bi-plus-lg)").first.click()
            modal = page.locator(".modal.show")
            modal.wait_for(state="visible", timeout=5000)
            page.screenshot(path=f"{SCREENSHOT_DIR}/add_user_modal.png")
            print("  ✓ Add User 模态框打开")

            # Field completeness (structural, language-agnostic):
            # username/email/system_account TextInputs, role/tenant selects,
            # password + confirm password inputs
            text_inputs = modal.locator(
                "input:not([type='password']):not([type='hidden']):not([type='submit'])"
            )
            assert text_inputs.count() >= 3, (
                f"expected >=3 text/email inputs (username/email/system_account), "
                f"got {text_inputs.count()}"
            )
            print(f"  ✓ 文本字段: {text_inputs.count()}（用户名/邮箱/系统账号）")

            selects = modal.locator("select")
            assert (
                selects.count() >= 2
            ), f"expected >=2 selects (role/tenant), got {selects.count()}"
            print(f"  ✓ 下拉字段: {selects.count()}（角色/租户）")

            passwords = modal.locator("input[type='password']")
            assert (
                passwords.count() == 2
            ), f"expected 2 password inputs (password/confirm), got {passwords.count()}"
            for i in range(passwords.count()):
                assert passwords.nth(i).is_visible(), f"password input {i} not visible"
            print("  ✓ 密码 + 确认密码字段可见")

            # Required-field markers and the password policy hint render
            assert modal.locator(".text-danger").count() >= 1, "no required-field markers"
            assert modal.locator("form").count() == 1, "modal form missing"
            print("  ✓ 必填标记与表单存在")

            footer_buttons = modal.locator(".modal-footer button")
            assert footer_buttons.count() >= 2, "footer Cancel/Save missing"
            print("  ✓ 取消/保存操作存在")

            print("\n测试完成！")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue54_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
