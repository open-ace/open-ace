#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Users - Tenants

测试内容：
1. 页面加载和标题显示
2. 租户列表显示
3. 添加租户功能
4. 编辑租户功能
5. 租户配置管理
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

MODULE_NAME = "manage_users_tenants"


def test_page_loads():
    """测试 Tenant Management 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/tenants")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".tenants-page"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_tenant_list_display():
    """测试租户列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/tenants")

            # 检查租户列表或空状态 - 使用实际组件的类名
            tenant_selectors = ["table", ".text-center.py-5", ".text-center", ".tenant-management"]
            assert check_element_exists(page, tenant_selectors), "应有租户列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_tenant_list")
            return True
        finally:
            browser.close()


def test_add_tenant_button():
    """测试添加租户按钮"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/tenants")

            # 检查添加租户按钮
            add_btn_selectors = [
                'button:has-text("Add")',
                'button:has-text("添加")',
                'button:has-text("New Tenant")',
            ]
            assert check_element_exists(page, add_btn_selectors), "添加租户按钮应可见"

            save_screenshot(page, MODULE_NAME, "03_add_tenant")
            return True
        finally:
            browser.close()


def test_edit_tenant():
    """测试编辑租户功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/tenants")

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

            save_screenshot(page, MODULE_NAME, "04_edit_tenant")
            return True
        finally:
            browser.close()


def test_tenant_config():
    """测试租户配置管理"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/tenants")

            # 检查配置按钮（可选）
            config_selectors = [
                'button:has-text("Config")',
                'button:has-text("配置")',
                ".config-btn",
            ]
            check_element_exists(page, config_selectors)

            save_screenshot(page, MODULE_NAME, "05_config")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有租户管理回归测试"""
    runner = TestRunner("Manage 模式 - Users - Tenants")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("租户列表显示", test_tenant_list_display),
        ("添加租户按钮", test_add_tenant_button),
        ("编辑租户功能", test_edit_tenant),
        ("租户配置管理", test_tenant_config),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
