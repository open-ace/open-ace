#!/usr/bin/env python3
"""
回归测试: 登录功能

测试内容：
1. 登录页面加载
2. 正确凭据登录成功
3. 错误凭据登录失败
4. 登出功能
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.sync_api import sync_playwright

from tests.e2e.regression.test_helpers import (
    BASE_URL,
    PAGE_LOAD_TIMEOUT_MS,
    TestRunner,
    check_element_exists,
    create_browser_context,
    save_screenshot,
)

MODULE_NAME = "login"
pytestmark = [pytest.mark.regression, pytest.mark.priority_p0]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")


def test_login_page_loads():
    """测试登录页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
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
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
            page.wait_for_selector("#username", state="visible", timeout=10000)

            # 输入凭据
            page.fill("#username", "admin")
            page.fill("#password", "admin123")
            page.click('button[type="submit"]')

            # 等待登录成功（bcrypt rounds=12 可能很慢）
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
            except Exception as e:
                if "/login" in page.url:
                    raise AssertionError(
                        f"Login did not redirect after 120s. Still on {page.url}. Error: {e}"
                    ) from e

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
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
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
    """测试登出功能

    Issue #2189: 修复假阳性问题
    - 移除 except Exception: pass
    - 找不到 logout 按钮必须失败
    - 使用多重 selector 策略增加稳定性
    - 明确的失败消息包含上下文信息
    """
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            # 先登录
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
            page.wait_for_selector("#username", state="visible", timeout=10000)
            page.fill("#username", "admin")
            page.fill("#password", "admin123")
            page.click('button[type="submit"]')

            # 等待登录成功
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
            except Exception as e:
                if "/login" in page.url:
                    raise AssertionError(
                        f"Login did not redirect after 120s during logout test. Error: {e}"
                    ) from e

            # 等待页面加载完成
            page.wait_for_load_state("networkidle", timeout=10000)

            # 查找登出按钮 - 使用多重 selector 策略
            logout_selectors = [
                'a[href="/logout"]',
                'button:has-text("Logout")',
                '.user-menu a:has-text("Logout")',
                'a:has-text("Sign out")',
                '[data-testid="logout-button"]',
            ]

            logout_clicked = False
            last_error = None

            for selector in logout_selectors:
                try:
                    btn = page.wait_for_selector(selector, timeout=5000, state="visible")
                    if btn and btn.is_visible():
                        btn.click()
                        logout_clicked = True
                        break
                except Exception as e:
                    last_error = e
                    continue

            # 验证登出按钮被找到并点击
            if not logout_clicked:
                pytest.fail(
                    f"Logout button not found with any selector. "
                    f"Last error: {last_error}. "
                    f"Current URL: {page.url}. "
                    f"Tried selectors: {logout_selectors}"
                )

            # 等待重定向到登录页面
            try:
                page.wait_for_url(lambda url: "/login" in url or url.endswith("/"), timeout=10000)
            except Exception:
                pytest.fail(
                    f"Logout did not redirect to login page. "
                    f"Current URL: {page.url}"
                )

            # 验证重定向到登录页面
            assert "/login" in page.url or page.url.endswith("/"), (
                f"登出后应重定向到登录页面或首页，当前URL: {page.url}"
            )

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
