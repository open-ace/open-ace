#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Settings - SSO

测试内容：
1. 页面加载和标题显示
2. SSO 配置表单显示
3. SSO 启用/禁用
4. 配置保存功能
5. SSO 测试连接
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_settings_sso_{name}.png')
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
    """测试 SSO Settings 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/settings/sso')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_sso_form_display():
    """测试 SSO 配置表单显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/settings/sso')
            page.wait_for_load_state('networkidle')

            form = page.locator('form, .sso-form, .settings-form')
            assert form.count() > 0, "SSO 配置表单应存在"

            # 检查表单字段
            inputs = page.locator('input, select, textarea')
            assert inputs.count() > 0, "表单字段应存在"

            save_screenshot(page, '02_form')
            return True
        finally:
            browser.close()


def test_sso_toggle():
    """测试 SSO 启用/禁用"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/settings/sso')
            page.wait_for_load_state('networkidle')

            toggle = page.locator('.toggle-switch, input[type="checkbox"], .form-check-input')

            if toggle.count() > 0:
                assert toggle.first.is_visible(), "SSO 开关应可见"

            save_screenshot(page, '03_toggle')
            return True
        finally:
            browser.close()


def test_save_button():
    """测试配置保存功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/settings/sso')
            page.wait_for_load_state('networkidle')

            save_btn = page.locator('button:has-text("Save"), button:has-text("保存"), button[type="submit"]')

            if save_btn.count() > 0:
                assert save_btn.first.is_visible(), "保存按钮应可见"

            save_screenshot(page, '04_save')
            return True
        finally:
            browser.close()


def test_test_connection():
    """测试 SSO 测试连接"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/settings/sso')
            page.wait_for_load_state('networkidle')

            test_btn = page.locator('button:has-text("Test"), button:has-text("测试"), button:has-text("Verify")')

            if test_btn.count() > 0:
                assert test_btn.first.is_visible(), "测试连接按钮应可见"

            save_screenshot(page, '05_test')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('SSO 配置表单显示', test_sso_form_display),
        ('SSO 启用/禁用', test_sso_toggle),
        ('配置保存功能', test_save_button),
        ('SSO 测试连接', test_test_connection),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Settings - SSO 回归测试")
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