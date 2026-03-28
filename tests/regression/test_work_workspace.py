#!/usr/bin/env python3
"""
回归测试: Work 模式 - Workspace

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 工具面板显示
4. 新建会话功能
5. 会话切换功能
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
    path = os.path.join(SCREENSHOT_DIR, f'work_workspace_{name}.png')
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
    """测试 Workspace 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work')
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
            page.goto(f'{BASE_URL}/work')
            page.wait_for_load_state('networkidle')

            session_list = page.locator('.session-list, .work-left-panel, .sessions-panel')
            assert session_list.count() > 0, "会话列表区域应存在"

            save_screenshot(page, '02_session_list')
            return True
        finally:
            browser.close()


def test_tools_panel_display():
    """测试工具面板显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work')
            page.wait_for_load_state('networkidle')

            tools_panel = page.locator('.work-right-panel, .assist-panel, .tools-panel')
            assert tools_panel.count() > 0, "工具面板区域应存在"

            save_screenshot(page, '03_tools_panel')
            return True
        finally:
            browser.close()


def test_new_session():
    """测试新建会话功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work')
            page.wait_for_load_state('networkidle')

            new_btn = page.locator('button:has-text("New"), button:has-text("新建"), .new-session-btn, button:has(.bi-plus)')

            if new_btn.count() > 0:
                assert new_btn.first.is_visible(), "新建会话按钮应可见"

            save_screenshot(page, '04_new_session')
            return True
        finally:
            browser.close()


def test_session_switch():
    """测试会话切换功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/work')
            page.wait_for_load_state('networkidle')

            session_item = page.locator('.session-item, .session-card').first()

            if session_item.is_visible():
                session_item.click()
                page.wait_for_timeout(500)

            save_screenshot(page, '05_session_switch')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('会话列表显示', test_session_list_display),
        ('工具面板显示', test_tools_panel_display),
        ('新建会话功能', test_new_session),
        ('会话切换功能', test_session_switch),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Work 模式 - Workspace 回归测试")
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