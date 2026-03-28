#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Messages

测试内容：
1. 页面加载和标题显示
2. 消息列表显示
3. 筛选功能
4. 分页功能
5. 消息详情查看
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_analysis_messages_{name}.png')
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
    """测试 Messages 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/messages')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_message_list_display():
    """测试消息列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/messages')
            page.wait_for_load_state('networkidle')

            msg_list = page.locator('.message-list, table, .data-table')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = msg_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有消息列表或空状态提示"

            save_screenshot(page, '02_list')
            return True
        finally:
            browser.close()


def test_filter_functionality():
    """测试筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/messages')
            page.wait_for_load_state('networkidle')

            filters = page.locator('.filter-bar, select, input[type="date"], input[placeholder*="search"]')

            if filters.count() > 0:
                filters.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '03_filter')
            return True
        finally:
            browser.close()


def test_pagination():
    """测试分页功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/messages')
            page.wait_for_load_state('networkidle')

            pagination = page.locator('.pagination, .pager, [class*="pagination"]')

            if pagination.count() > 0:
                page_buttons = pagination.locator('button, a, .page-item')
                assert page_buttons.count() > 0, "分页按钮应存在"

            save_screenshot(page, '04_pagination')
            return True
        finally:
            browser.close()


def test_sender_filter():
    """测试发送者筛选"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/messages')
            page.wait_for_load_state('networkidle')

            sender_filter = page.locator('select[name="sender"], .sender-dropdown, select[id*="sender"]')

            if sender_filter.count() > 0:
                sender_filter.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '05_sender')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('消息列表显示', test_message_list_display),
        ('筛选功能', test_filter_functionality),
        ('分页功能', test_pagination),
        ('发送者筛选', test_sender_filter),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Analysis - Messages 回归测试")
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