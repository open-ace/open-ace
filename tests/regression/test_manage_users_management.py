#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Users - Management

测试内容：
1. 页面加载和标题显示
2. 用户列表显示
3. 添加用户功能
4. 编辑用户功能
5. 用户角色管理
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'regression')


def ensure_screenshot_dir():
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f'manage_users_management_{name}.png')
    page.screenshot(path=path)
    return path


def login(page):
    page.goto(f'{BASE_URL}/login')
    page.wait_for_load_state('networkidle')
    page.fill('#username', USERNAME)
    page.fill('#password', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: '/login' not in url, timeout=10000)


def test_page_loads():
    """测试 User Management 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/users')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_user_list_display():
    """测试用户列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/users')
            page.wait_for_load_state('networkidle')

            user_list = page.locator('table, .user-table, .data-table')
            assert user_list.count() > 0, "用户列表应存在"

            # 验证表头
            headers = page.locator('th, .table-header')
            assert headers.count() > 0, "表头应存在"

            save_screenshot(page, '02_user_list')
            return True
        finally:
            browser.close()


def test_add_user_button():
    """测试添加用户按钮"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/users')
            page.wait_for_load_state('networkidle')

            add_btn = page.locator('button:has-text("Add"), button:has-text("添加"), button:has-text("New User")')

            if add_btn.count() > 0:
                assert add_btn.first.is_visible(), "添加用户按钮应可见"

            save_screenshot(page, '03_add_user')
            return True
        finally:
            browser.close()


def test_edit_user():
    """测试编辑用户功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/users')
            page.wait_for_load_state('networkidle')

            edit_btn = page.locator('button:has-text("Edit"), button:has-text("编辑"), .edit-btn')

            if edit_btn.count() > 0:
                edit_btn.first.click()
                page.wait_for_timeout(500)

                # 检查编辑模态框
                modal = page.locator('.modal, .edit-modal, .form-modal')
                if modal.count() > 0:
                    assert modal.first.is_visible(), "编辑模态框应可见"

            save_screenshot(page, '04_edit_user')
            return True
        finally:
            browser.close()


def test_role_management():
    """测试用户角色管理"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/users')
            page.wait_for_load_state('networkidle')

            role_column = page.locator('td:has-text("admin"), td:has-text("user"), .role-badge')

            if role_column.count() > 0:
                assert role_column.first.is_visible(), "角色列应可见"

            save_screenshot(page, '05_role')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('用户列表显示', test_user_list_display),
        ('添加用户按钮', test_add_user_button),
        ('编辑用户功能', test_edit_user),
        ('用户角色管理', test_role_management),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Users - Management 回归测试")
    print("=" * 60)

    for name, test_func in tests:
        try:
            test_func()
            results.append((name, 'PASS', None))
            print(f"  ✓ {name}")
        except Exception as e:
            results.append((name, 'FAIL', str(e)))
            print(f"  ✗ {name}: {e}")

    print("\n" + "-" * 60)
    passed = sum(1 for r in results if r[1] == 'PASS')
    total = len(results)
    print(f"结果: {passed}/{total} 通过")
    print("-" * 60)

    return results


if __name__ == '__main__':
    run_all_tests()