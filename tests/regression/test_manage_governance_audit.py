#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Audit Center

测试内容：
1. 页面加载和标题显示
2. 审计日志列表显示
3. 时间筛选功能
4. 用户筛选功能
5. 日志详情查看
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_governance_audit_{name}.png')
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
    """测试 Audit Center 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/audit')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_audit_log_list():
    """测试审计日志列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/audit')
            page.wait_for_load_state('networkidle')

            log_list = page.locator('.audit-log-list, table, .data-table')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = log_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有审计日志列表或空状态提示"

            save_screenshot(page, '02_list')
            return True
        finally:
            browser.close()


def test_time_filter():
    """测试时间筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/audit')
            page.wait_for_load_state('networkidle')

            time_filter = page.locator('input[type="date"], .date-picker, select[name*="time"]')

            if time_filter.count() > 0:
                time_filter.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '03_time_filter')
            return True
        finally:
            browser.close()


def test_user_filter():
    """测试用户筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/audit')
            page.wait_for_load_state('networkidle')

            user_filter = page.locator('select[name*="user"], .user-filter')

            if user_filter.count() > 0:
                user_filter.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '04_user_filter')
            return True
        finally:
            browser.close()


def test_log_detail():
    """测试日志详情查看"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/audit')
            page.wait_for_load_state('networkidle')

            log_item = page.locator('.audit-log-item, tr, .list-item').first()

            if log_item.is_visible():
                log_item.click()
                page.wait_for_timeout(500)

                detail = page.locator('.audit-detail, .modal, .detail-panel')
                if detail.count() > 0:
                    assert detail.first.is_visible(), "日志详情应可见"

            save_screenshot(page, '05_detail')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('审计日志列表显示', test_audit_log_list),
        ('时间筛选功能', test_time_filter),
        ('用户筛选功能', test_user_filter),
        ('日志详情查看', test_log_detail),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Governance - Audit Center 回归测试")
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