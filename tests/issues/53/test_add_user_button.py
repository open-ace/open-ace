#!/usr/bin/env python3
"""
UI Test for Issue 53: Management页面User Management的Add User按钮点不了

测试内容：
1. 登录系统
2. 导航到用户管理页（/manage/users）
3. 检查 Add User 按钮存在且可点击
4. 点击 Add User 按钮
5. 验证 Add User 模态框弹出（标题 + 取消/保存操作）

#2457 realignment: the baselined TypeError (object NoneType can't be used in
'await' expression) came from the async script's missing awaits; the retired
#nav-management navigation and #add-user-btn id are gone. Converted to the
sync API and the current markup: the create-user entry is the bi-plus-lg
button on /manage/users, and the shared Modal renders .modal.show.d-block
with a Cancel/Save footer.
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
SCREENSHOT_DIR = "screenshots/issues/53"


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


def test_issue53():
    """Test Issue 53: Add User button functionality in Management Users tab"""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            print("\n[Step 1] 登录系统")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            print("  ✓ 登录成功")

            print("\n[Step 2] 导航到用户管理页")
            page.goto(f"{BASE_URL}/manage/users", wait_until="networkidle")
            page.wait_for_selector("table, .empty-state", timeout=TIMEOUT)
            print("  ✓ 用户管理页加载完成")

            print("\n[Step 3] 检查 Add User 按钮存在且可点击")
            add_btn = page.locator("button:has(i.bi-plus-lg)").first
            assert add_btn.count() > 0, "Add User button not found"
            assert add_btn.is_visible(), "Add User button not visible"
            assert add_btn.is_enabled(), "Add User button not enabled"
            print(f"  ✓ 按钮可点击: '{add_btn.inner_text().strip()}'")

            print("\n[Step 4] 点击 Add User 按钮")
            add_btn.click()
            modal = page.locator(".modal.show")
            modal.wait_for(state="visible", timeout=5000)
            assert page.locator(".modal-backdrop").count() >= 1, "modal backdrop missing"
            print("  ✓ 模态框弹出")

            print("\n[Step 5] 验证模态框操作（标题 + 取消关闭）")
            modal_title = page.locator(".modal-title, .modal h5, .modal h4").first
            assert modal_title.is_visible(), "modal title not visible"
            print(f"  ✓ 标题: '{modal_title.inner_text().strip()}'")
            page.screenshot(path=f"{SCREENSHOT_DIR}/add_user_modal.png")

            footer_buttons = modal.locator(".modal-footer button")
            assert (
                footer_buttons.count() >= 2
            ), f"expected >=2 footer buttons, got {footer_buttons.count()}"
            footer_buttons.first.click()  # Cancel
            modal.wait_for(state="detached", timeout=5000)
            assert not page.locator(".modal.show").count(), "modal did not close on cancel"
            print("  ✓ 取消按钮关闭模态框")

            print("\n测试完成！")

        except Exception as e:
            page.screenshot(path=f"{SCREENSHOT_DIR}/issue53_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
