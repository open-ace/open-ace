#!/usr/bin/env python3
"""
回归测试: 登录功能

测试内容：
1. 登录页面加载
2. 正确凭据登录成功
3. 错误凭据登录失败
4. 登录后重定向
5. 登出功能
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
    path = os.path.join(SCREENSHOT_DIR, f'login_{name}.png')
    page.screenshot(path=path)
    return path


def test_login_page_loads():
    """测试登录页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')

            # 验证登录页面元素
            assert page.locator('#username').is_visible(), "用户名输入框应可见"
            assert page.locator('#password').is_visible(), "密码输入框应可见"
            assert page.locator('button[type="submit"]').is_visible(), "登录按钮应可见"

            save_screenshot(page, '01_login_page')
            return True
        finally:
            browser.close()


def test_login_success():
    """测试正确凭据登录成功"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')

            # 输入凭据
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('button[type="submit"]')

            # 等待重定向
            page.wait_for_url(lambda url: '/login' not in url, timeout=10000)

            # 验证登录成功
            assert '/login' not in page.url, "登录后应重定向到其他页面"

            save_screenshot(page, '02_login_success')
            return True
        finally:
            browser.close()


def test_login_failure():
    """测试错误凭据登录失败"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')

            # 输入错误凭据
            page.fill('#username', 'wronguser')
            page.fill('#password', 'wrongpass')
            page.click('button[type="submit"]')

            # 等待响应
            page.wait_for_timeout(1000)

            # 验证仍在登录页面
            assert '/login' in page.url, "登录失败应停留在登录页面"

            save_screenshot(page, '03_login_failure')
            return True
        finally:
            browser.close()


def test_logout():
    """测试登出功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            # 先登录
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: '/login' not in url, timeout=10000)

            # 查找并点击登出按钮
            logout_btn = page.locator('a[href="/logout"], button:has-text("Logout"), .user-menu a:has-text("Logout")')
            if logout_btn.count() > 0:
                logout_btn.first.click()
                page.wait_for_load_state('networkidle')

                # 验证重定向到登录页面
                assert '/login' in page.url, "登出后应重定向到登录页面"

            save_screenshot(page, '04_logout')
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有登录回归测试"""
    tests = [
        ('登录页面加载', test_login_page_loads),
        ('正确凭据登录', test_login_success),
        ('错误凭据登录失败', test_login_failure),
        ('登出功能', test_logout),
    ]

    results = []
    print("\n" + "=" * 60)
    print("登录功能回归测试")
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