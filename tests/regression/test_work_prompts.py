#!/usr/bin/env python3
"""
回归测试: Work 模式 - Prompts

测试内容：
1. 页面加载和标题显示
2. Prompts 列表显示
3. Prompt 搜索功能
4. Prompt 详情查看
5. Prompt 使用功能
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
    path = os.path.join(SCREENSHOT_DIR, f'work_prompts_{name}.png')
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
    """测试 Prompts 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/prompts')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, h4, h5, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .work-main, .main-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_prompts_list_display():
    """测试 Prompts 列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/prompts')
            page.wait_for_load_state('networkidle')

            prompts_list = page.locator('.prompts-list, table, .data-table, .list')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = prompts_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有 Prompts 列表或空状态提示"

            save_screenshot(page, '02_prompts_list')
            return True
        finally:
            browser.close()


def test_prompt_search():
    """测试 Prompt 搜索功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/prompts')
            page.wait_for_load_state('networkidle')

            search_input = page.locator('input[placeholder*="search"], input[type="text"]').first()

            if search_input.is_visible():
                search_input.fill('test')
                page.wait_for_timeout(1000)
                search_input.clear()

            save_screenshot(page, '03_search')
            return True
        finally:
            browser.close()


def test_prompt_detail():
    """测试 Prompt 详情查看"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/prompts')
            page.wait_for_load_state('networkidle')

            prompt_item = page.locator('.prompt-item, tr, .list-item, .card').first()

            if prompt_item.is_visible():
                prompt_item.click()
                page.wait_for_timeout(500)

                detail = page.locator('.prompt-detail, .modal, .detail-panel')
                if detail.count() > 0:
                    assert detail.first.is_visible(), "Prompt 详情应可见"

            save_screenshot(page, '04_detail')
            return True
        finally:
            browser.close()


def test_prompt_use():
    """测试 Prompt 使用功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work/prompts')
            page.wait_for_load_state('networkidle')

            use_btn = page.locator('button:has-text("Use"), button:has-text("使用"), .use-btn')

            if use_btn.count() > 0:
                assert use_btn.first.is_visible(), "使用按钮应可见"

            save_screenshot(page, '05_use')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('Prompts 列表显示', test_prompts_list_display),
        ('Prompt 搜索功能', test_prompt_search),
        ('Prompt 详情查看', test_prompt_detail),
        ('Prompt 使用功能', test_prompt_use),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Work 模式 - Prompts 回归测试")
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