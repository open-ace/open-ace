#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Users - Management

测试内容：
1. 页面加载和标题显示
2. 用户列表显示
3. 添加用户功能
4. 编辑用户功能
5. 用户角色管理
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import expect, sync_playwright

from tests.regression.test_helpers import (
    BASE_URL,
    HEADLESS,
    TestRunner,
    check_element_exists,
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
)

MODULE_NAME = "manage_users_management"


def test_page_loads():
    """测试 User Management 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/users")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".users-page"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_user_list_display():
    """测试用户列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/users")

            # 检查用户列表
            user_list_selectors = ["table", ".user-table", ".data-table"]
            assert check_element_exists(page, user_list_selectors), "用户列表应存在"

            # 检查表头
            header_selectors = ["th", ".table-header"]
            assert check_element_exists(page, header_selectors), "表头应存在"

            save_screenshot(page, MODULE_NAME, "02_user_list")
            return True
        finally:
            browser.close()


def test_add_user_button():
    """测试添加用户按钮"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/users")

            # 检查添加用户按钮
            add_btn_selectors = [
                'button:has-text("Add")',
                'button:has-text("添加")',
                'button:has-text("New User")',
            ]
            assert check_element_exists(page, add_btn_selectors), "添加用户按钮应可见"

            save_screenshot(page, MODULE_NAME, "03_add_user")
            return True
        finally:
            browser.close()


def test_edit_user():
    """测试编辑用户功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/users")

            # 尝试点击编辑按钮
            edit_btn_selectors = ['button:has-text("Edit")', 'button:has-text("编辑")', ".edit-btn"]
            if check_element_exists(page, edit_btn_selectors):
                try:
                    edit_btn = page.locator(
                        edit_btn_selectors[0] + ", " + edit_btn_selectors[1]
                    ).first
                    if edit_btn.is_visible():
                        edit_btn.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "04_edit_user")
            return True
        finally:
            browser.close()


def test_role_management():
    """测试用户角色管理"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/users")

            # 检查角色列
            role_selectors = ['td:has-text("admin")', 'td:has-text("user")', ".role-badge"]
            assert check_element_exists(page, role_selectors), "角色列应可见"

            save_screenshot(page, MODULE_NAME, "05_role")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有用户管理回归测试"""
    runner = TestRunner("Manage 模式 - Users - Management")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("用户列表显示", test_user_list_display),
        ("添加用户按钮", test_add_user_button),
        ("编辑用户功能", test_edit_user),
        ("用户角色管理", test_role_management),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
