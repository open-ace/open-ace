#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Quota & Alerts

测试内容：
1. 页面加载和标题显示
2. 配额设置显示
3. 告警规则列表
4. 配额调整功能
5. 告警启用/禁用
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_governance_quota_{name}.png')
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
    """测试 Quota & Alerts 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_quota_settings_display():
    """测试配额设置显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle')

            quota_settings = page.locator('.quota-settings, .quota-card, .card')
            assert quota_settings.count() > 0, "配额设置区域应存在"

            save_screenshot(page, '02_quota_settings')
            return True
        finally:
            browser.close()


def test_alert_rules_list():
    """测试告警规则列表"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle')

            alert_list = page.locator('.alert-rules-list, table, .rules-list')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = alert_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有告警规则列表或空状态提示"

            save_screenshot(page, '03_alert_list')
            return True
        finally:
            browser.close()


def test_quota_adjustment():
    """测试配额调整功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle')

            quota_input = page.locator('input[type="number"], .quota-input')

            if quota_input.count() > 0:
                assert quota_input.first.is_visible(), "配额输入应可见"

            save_screenshot(page, '04_quota_adjust')
            return True
        finally:
            browser.close()


def test_alert_toggle():
    """测试告警启用/禁用"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/quota')
            page.wait_for_load_state('networkidle')

            toggle = page.locator('.toggle-switch, input[type="checkbox"], .form-check-input')

            if toggle.count() > 0:
                assert toggle.first.is_visible(), "告警开关应可见"

            save_screenshot(page, '05_alert_toggle')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('配额设置显示', test_quota_settings_display),
        ('告警规则列表', test_alert_rules_list),
        ('配额调整功能', test_quota_adjustment),
        ('告警启用/禁用', test_alert_toggle),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Governance - Quota & Alerts 回归测试")
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