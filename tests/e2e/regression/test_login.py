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

from playwright.sync_api import TimeoutError, sync_playwright

from tests.e2e.regression.test_helpers import (
    BASE_URL,
    PAGE_LOAD_TIMEOUT_MS,
    PASSWORD,
    TestRunner,
    check_element_exists,
    create_browser_context,
    dismiss_force_change_password_modal,
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

        finally:
            browser.close()


def test_login_success():
    """测试正确凭据登录成功"""
    import tests.e2e.regression.test_helpers as helpers

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
            page.fill("#password", helpers.PASSWORD)
            page.click('button[type="submit"]')

            # 等待登录成功（bcrypt rounds=12 可能很慢）
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
            except Exception as e:  # allow-swallow: UI element may not exist
                if "/login" in page.url:
                    raise AssertionError(
                        f"Login did not redirect after 120s. Still on {page.url}. Error: {e}"
                    ) from e

            # 验证登录成功
            assert "/login" not in page.url, "登录后应重定向到其他页面"

            save_screenshot(page, MODULE_NAME, "02_login_success")

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

        finally:
            browser.close()


def test_logout():
    """测试登出功能"""
    import tests.e2e.regression.test_helpers as helpers

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
            page.fill("#password", helpers.PASSWORD)
            page.click('button[type="submit"]')
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
            except Exception as e:  # allow-swallow: UI element may not exist
                if "/login" in page.url:
                    raise AssertionError(
                        f"Login did not redirect after 120s during logout test. Error: {e}"
                    ) from e

            # Handle force change password modal (default admin has must_change_password=true)
            dismiss_force_change_password_modal(page)

            # Issue #2189: Logout button is inside a dropdown menu
            # Need to first click the user avatar to open the dropdown
            # Header structure: div.dropdown > button.dropdown-toggle > ul.dropdown-menu > li > button.dropdown-item
            logout_found = False
            try:
                # Click user avatar button to open dropdown menu.
                # Use .header-icon-btn.dropdown-toggle as primary selector (always present
                # on the user menu button regardless of avatar vs icon).
                # Fallback: .dropdown-toggle:has(.bi-person-circle) for icon-only mode.
                user_menu_btn = page.wait_for_selector(
                    ".header-icon-btn.dropdown-toggle, " ".dropdown-toggle:has(.bi-person-circle)",
                    state="visible",
                    timeout=15000,
                )
                user_menu_btn.click()

                # Wait for dropdown menu to be visible (not just a fixed timeout)
                page.wait_for_selector(
                    ".dropdown-menu.show, .dropdown-menu",
                    state="visible",
                    timeout=3000,
                )

                # Click the logout button in the dropdown menu.
                # Use text-based selectors (Logout / 退出登录) plus icon-based
                # fallback (.bi-box-arrow-right) for multi-language robustness.
                logout_btn = page.wait_for_selector(
                    "button.dropdown-item:has-text('Logout'), "
                    "button.dropdown-item:has-text('退出登录'), "
                    "button.dropdown-item:has(.bi-box-arrow-right)",
                    state="visible",
                    timeout=5000,
                )

                # Ensure the button is still attached before clicking
                if logout_btn and logout_btn.is_visible():
                    # Use evaluate to click via JS to avoid "not attached" errors
                    logout_btn.evaluate("el => el.click()")
                    logout_found = True
            except (TimeoutError, Exception) as e:
                # Log the error for debugging but continue to handle failure
                print(f"Logout button interaction failed: {e}")
                logout_found = False

            # Issue #2189: 找不到必须失败
            if not logout_found:
                save_screenshot(page, MODULE_NAME, "04_logout_no_button")
                pytest.fail(
                    "Logout button not found. Check if user menu dropdown is working. "
                    "Expected: click user avatar -> see dropdown -> click logout button"
                )

            # Issue #2189: 等待重定向完成（使用明确条件）
            try:
                page.wait_for_url("**/login**", timeout=10000)
            except TimeoutError:
                save_screenshot(page, MODULE_NAME, "04_logout_no_redirect")
                pytest.fail(f"Logout did not redirect to login page. Current URL: {page.url}")

            # Issue #2189: 最终断言
            assert "/login" in page.url, f"Expected login URL, got {page.url}"

            save_screenshot(page, MODULE_NAME, "04_logout_success")

        except Exception:
            # 保存失败截图
            save_screenshot(page, MODULE_NAME, "04_logout_error")
            raise  # 重新抛出，不吞掉

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
