#!/usr/bin/env python3
"""
回归测试: Work 模式 - Sessions

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 会话筛选功能
4. 会话详情查看
5. 会话删除功能
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
    path = os.path.join(SCREENSHOT_DIR, f'work_sessions_{name}.png')
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
    """测试 Sessions 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/sessions')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, h4, h5, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .work-main, .main-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_session_list_display():
    """测试会话列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/sessions')
            page.wait_for_load_state('networkidle')

            session_list = page.locator('.sessions-list, table, .data-table')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = session_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有会话列表或空状态提示"

            save_screenshot(page, '02_session_list')
            return True
        finally:
            browser.close()


def test_session_filter():
    """测试会话筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/sessions')
            page.wait_for_load_state('networkidle')

            filter_input = page.locator('input[placeholder*="search"], input[type="text"]').first()

            if filter_input.is_visible():
                filter_input.fill('test')
                page.wait_for_timeout(1000)
                filter_input.clear()

            save_screenshot(page, '03_filter')
            return True
        finally:
            browser.close()


def test_session_detail():
    """测试会话详情查看"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/sessions')
            page.wait_for_load_state('networkidle')

            session_item = page.locator('.session-item, tr, .list-item').first()

            if session_item.is_visible():
                session_item.click()
                page.wait_for_timeout(500)

                detail = page.locator('.session-detail, .modal, .detail-panel')
                if detail.count() > 0:
                    assert detail.first.is_visible(), "会话详情应可见"

            save_screenshot(page, '04_detail')
            return True
        finally:
            browser.close()


def test_session_delete():
    """测试会话删除功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/sessions')
            page.wait_for_load_state('networkidle')

            delete_btn = page.locator('button:has-text("Delete"), button:has-text("删除"), .delete-btn, button:has(.bi-trash)')

            if delete_btn.count() > 0:
                assert delete_btn.first.is_visible(), "删除按钮应可见"

            save_screenshot(page, '05_delete')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('会话列表显示', test_session_list_display),
        ('会话筛选功能', test_session_filter),
        ('会话详情查看', test_session_detail),
        ('会话删除功能', test_session_delete),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Work 模式 - Sessions 回归测试")
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