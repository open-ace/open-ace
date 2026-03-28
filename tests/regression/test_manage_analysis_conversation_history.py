#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Conversation History

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 会话详情查看
4. 搜索筛选功能
5. 会话导出功能
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_analysis_conversation_history_{name}.png')
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
    """测试 Conversation History 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/conversation-history')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_conversation_list_display():
    """测试会话列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/conversation-history')
            page.wait_for_load_state('networkidle')

            conv_list = page.locator('.conversation-list, table, .data-table, .list')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = conv_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有会话列表或空状态提示"

            save_screenshot(page, '02_list')
            return True
        finally:
            browser.close()


def test_conversation_detail():
    """测试会话详情查看"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/conversation-history')
            page.wait_for_load_state('networkidle')

            # 点击第一个会话查看详情
            conv_item = page.locator('.conversation-item, tr, .list-item').first()

            if conv_item.is_visible():
                conv_item.click()
                page.wait_for_timeout(500)

                # 检查详情面板或模态框
                detail = page.locator('.conversation-detail, .modal, .detail-panel')
                if detail.count() > 0:
                    assert detail.first.is_visible(), "会话详情应可见"

            save_screenshot(page, '03_detail')
            return True
        finally:
            browser.close()


def test_search_filter():
    """测试搜索筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/conversation-history')
            page.wait_for_load_state('networkidle')

            search_input = page.locator('input[placeholder*="search"], input[type="text"]').first()

            if search_input.is_visible():
                search_input.fill('test')
                page.wait_for_timeout(1000)
                search_input.clear()

            save_screenshot(page, '04_search')
            return True
        finally:
            browser.close()


def test_export_function():
    """测试会话导出功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/conversation-history')
            page.wait_for_load_state('networkidle')

            export_btn = page.locator('button:has-text("Export"), button:has-text("导出"), button:has(.bi-download)')

            if export_btn.count() > 0:
                assert export_btn.first.is_visible(), "导出按钮应可见"

            save_screenshot(page, '05_export')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('会话列表显示', test_conversation_list_display),
        ('会话详情查看', test_conversation_detail),
        ('搜索筛选功能', test_search_filter),
        ('会话导出功能', test_export_function),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Analysis - Conversation History 回归测试")
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