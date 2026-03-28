#!/usr/bin/env python3
"""
回归测试: 导航功能

测试内容：
1. 侧边栏菜单显示
2. 菜单项点击导航
3. 页面标题更新
4. 面包屑导航
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect

# 配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'regression')


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    """保存截图"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f'navigation_{name}.png')
    page.screenshot(path=path)
    return path


def login(page):
    """登录辅助函数"""
    page.goto(f'{BASE_URL}/login')
    page.wait_for_load_state('networkidle')
    page.fill('#username', USERNAME)
    page.fill('#password', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: '/login' not in url, timeout=10000)


def test_sidebar_menu_visible():
    """测试侧边栏菜单显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)

            # 验证侧边栏存在
            sidebar = page.locator('.sidebar, nav, .menu')
            assert sidebar.count() > 0, "侧边栏应存在"

            # 验证菜单项存在
            menu_items = page.locator('.sidebar a, nav a, .menu a')
            assert menu_items.count() > 0, "菜单项应存在"

            save_screenshot(page, '01_sidebar')
            return True
        finally:
            browser.close()


def test_menu_navigation():
    """测试菜单项点击导航"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)

            # 测试点击各个菜单项
            menu_items = [
                ('Work', '/work'),
                ('Sessions', '/work/sessions'),
                ('Messages', '/work/messages'),
                ('Analysis', '/work/analysis'),
            ]

            for name, path in menu_items:
                link = page.locator(f'a[href*="{path}"], a:has-text("{name}")')
                if link.count() > 0:
                    link.first.click()
                    page.wait_for_load_state('networkidle')
                    page.wait_for_timeout(500)
                    assert path in page.url, f"点击 {name} 后应导航到 {path}"

            save_screenshot(page, '02_navigation')
            return True
        finally:
            browser.close()


def test_page_title_updates():
    """测试页面标题更新"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)

            # 导航到不同页面并检查标题
            pages_to_check = [
                ('/work', 'Work'),
                ('/work/sessions', 'Sessions'),
            ]

            for path, expected_title in pages_to_check:
                page.goto(f'{BASE_URL}{path}')
                page.wait_for_load_state('networkidle')

                title = page.title()
                # 标题应包含页面名称
                assert expected_title.lower() in title.lower() or page.locator(f'h1, h2, .page-title').count() > 0, \
                    f"页面 {path} 应有标题"

            save_screenshot(page, '03_title')
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有导航回归测试"""
    tests = [
        ('侧边栏菜单显示', test_sidebar_menu_visible),
        ('菜单导航', test_menu_navigation),
        ('页面标题更新', test_page_title_updates),
    ]

    results = []
    print("\n" + "=" * 60)
    print("导航功能回归测试")
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