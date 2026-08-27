"""
Test script for issue 80: Manage 模式语言切换下拉菜单项可见性

#2457 realignment: the baselined failure was ERR_CONNECTION_REFUSED —
BASE_URL was hardcoded to a fixed port. Now honors the exported BASE_URL,
clears the seeded admin password gate, skips without a reachable server,
and drives the current Header language dropdown (the globe
header-icon-btn + ul.dropdown-menu with EN/中文/日本語 dropdown-item
buttons). The original color-visibility contract is asserted functionally:
every menu item is visible with readable (non-background-matching) text,
the current language carries the active class, and switching languages
moves the active class.
"""

import os
import re

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(80)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
TIMEOUT = 15000


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
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _open_language_menu(page):
    """Open the Header language dropdown; return the menu locator."""
    toggle = page.locator("button.header-icon-btn.dropdown-toggle").filter(
        has=page.locator("i.bi-globe")
    )
    toggle.first.click()
    menu = page.locator("ul.dropdown-menu").first
    menu.wait_for(state="visible", timeout=5000)
    return menu


def test_manage_language_dropdown():
    """测试 Manage 模式下的语言切换下拉列表"""
    _skip_if_no_server()
    _clear_seeded_password_gate()
    print("=" * 60)
    print("测试 Manage 模式下的语言切换下拉列表")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url(re.compile(r".*/manage"), timeout=15000)
            print("✓ 登录成功（落在 Manage 模式）")

            page.goto(f"{BASE_URL}/manage/dashboard", wait_until="networkidle")
            print("✓ Manage dashboard 加载完成")

            menu = _open_language_menu(page)
            items = menu.locator("button.dropdown-item")
            count = items.count()
            print(f"   找到 {count} 个语言选项")
            assert count >= 3, f"expected >=3 language items, got {count}"

            # Visibility contract: every item is visible with readable text —
            # text color must differ from the background it sits on (the
            # original complaint was invisible menu text)
            active_index = None
            for i in range(count):
                item = items.nth(i)
                assert item.is_visible(), f"language item {i} not visible"
                text = item.inner_text().strip()
                assert text, f"language item {i} has empty text"
                color = item.evaluate("el => getComputedStyle(el).color")
                bg = item.evaluate("el => getComputedStyle(el).backgroundColor")
                assert color != bg, (
                    f"language item '{text}' text color matches background "
                    f"({color} on {bg}) — invisible text"
                )
                classes = item.get_attribute("class") or ""
                if "active" in classes:
                    active_index = i
                print(f"   ✓ 语言项 {i + 1}: '{text}' ({color} on {bg})")

            assert active_index is not None, "no language item carries the active class"

            # Switching languages moves the active class
            target = (active_index + 1) % count
            items.nth(target).click()
            page.wait_for_timeout(500)
            menu = _open_language_menu(page)
            items = menu.locator("button.dropdown-item")
            new_classes = items.nth(target).get_attribute("class") or ""
            assert "active" in new_classes, "clicked language did not become active"
            print(f"✓ 切换语言后 active 项移动到 '{items.nth(target).inner_text().strip()}'")

            # restore the original language
            items.nth(active_index).click()
            page.wait_for_timeout(500)
            print("✓ 已恢复原语言")

            page.screenshot(path="screenshots/issues/80/language_dropdown.png")
            print("✓ 截图保存")
            print("测试完成！")

        except Exception as e:
            page.screenshot(path="screenshots/issues/80/language_dropdown_error.png")
            print(f"\n✗ Test failed: {e}")
            raise
        finally:
            browser.close()
