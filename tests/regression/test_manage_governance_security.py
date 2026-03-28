#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Security Center

测试内容：
1. 页面加载和标题显示
2. 安全概览显示
3. 安全事件列表
4. 安全设置
5. 安全报告
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_governance_security_{name}.png')
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
    """测试 Security Center 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/security')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_security_overview():
    """测试安全概览显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/security')
            page.wait_for_load_state('networkidle')

            overview = page.locator('.security-overview, .overview-card, .card')
            assert overview.count() > 0, "安全概览区域应存在"

            save_screenshot(page, '02_overview')
            return True
        finally:
            browser.close()


def test_security_events_list():
    """测试安全事件列表"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/security')
            page.wait_for_load_state('networkidle')

            events_list = page.locator('.security-events-list, table, .data-table')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = events_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有安全事件列表或空状态提示"

            save_screenshot(page, '03_events_list')
            return True
        finally:
            browser.close()


def test_security_settings():
    """测试安全设置"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/security')
            page.wait_for_load_state('networkidle')

            settings = page.locator('.security-settings, .settings-section, form')

            if settings.count() > 0:
                assert settings.first.is_visible(), "安全设置区域应可见"

            save_screenshot(page, '04_settings')
            return True
        finally:
            browser.close()


def test_security_report():
    """测试安全报告"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/security')
            page.wait_for_load_state('networkidle')

            report_btn = page.locator('button:has-text("Report"), button:has-text("报告"), button:has(.bi-file-earmark)')

            if report_btn.count() > 0:
                assert report_btn.first.is_visible(), "报告按钮应可见"

            save_screenshot(page, '05_report')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('安全概览显示', test_security_overview),
        ('安全事件列表', test_security_events_list),
        ('安全设置', test_security_settings),
        ('安全报告', test_security_report),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Governance - Security Center 回归测试")
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