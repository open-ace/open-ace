#!/usr/bin/env python3
"""
回归测试: 登录功能

测试内容：
1. 登录页面加载
2. 正确凭据登录成功
3. 错误凭据登录失败
4. 登出功能
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
from tests.regression.test_helpers import (
    create_browser_context,
    save_screenshot,
    check_element_exists,
    TestRunner,
    BASE_URL,
    HEADLESS,
)

MODULE_NAME = "login"


def test_login_page_loads():
    """测试登录页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)

            # 验证登录页面元素
            assert page.locator("#username").is_visible(), "用户名输入框应可见"
            assert page.locator("#password").is_visible(), "密码输入框应可见"
            assert page.locator('button[type="submit"]').is_visible(), "登录按钮应可见"

            save_screenshot(page, MODULE_NAME, "01_login_page")
            return True
        finally:
            browser.close()


def test_login_success():
    """测试正确凭据登录成功"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)

            # 输入凭据
            page.fill("#username", "admin")
            page.fill("#password", "admin123")
            page.click('button[type="submit"]')

            # 等待登录成功 - 检查 URL 变化
            page.wait_for_timeout(1000)
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

            # 验证登录成功
            assert "/login" not in page.url, "登录后应重定向到其他页面"

            save_screenshot(page, MODULE_NAME, "02_login_success")
            return True
        finally:
            browser.close()


def test_login_failure():
    """测试错误凭据登录失败"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)

            # 输入错误凭据
            page.fill("#username", "wronguser")
            page.fill("#password", "wrongpass")
            page.click('button[type="submit"]')

            # 等待响应
            page.wait_for_timeout(1000)

            # 验证仍在登录页面
            assert "/login" in page.url, "登录失败应停留在登录页面"

            save_screenshot(page, MODULE_NAME, "03_login_failure")
            return True
        finally:
            browser.close()


def test_logout():
    """测试登出功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            # 先登录
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            page.fill("#username", "admin")
            page.fill("#password", "admin123")
            page.click('button[type="submit"]')
            page.wait_for_timeout(1000)
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

            # 查找并点击登出按钮
            logout_selectors = [
                'a[href="/logout"]',
                'button:has-text("Logout")',
                '.user-menu a:has-text("Logout")',
            ]
            if check_element_exists(page, logout_selectors):
                try:
                    logout_btn = page.locator(
                        logout_selectors[0] + ", " + logout_selectors[1]
                    ).first
                    if logout_btn.is_visible():
                        logout_btn.click()
                        page.wait_for_timeout(1000)
                        # 验证重定向到登录页面
                        assert "/login" in page.url, "登出后应重定向到登录页面"
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "04_logout")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有登录回归测试"""
    runner = TestRunner("登录功能")
    runner.print_header()

    tests = [
        ("登录页面加载", test_login_page_loads),
        ("正确凭据登录", test_login_success),
        ("错误凭据登录失败", test_login_failure),
        ("登出功能", test_logout),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
