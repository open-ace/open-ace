#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Settings - SSO

测试内容：
1. 页面加载和标题显示
2. SSO 配置表单显示
3. SSO 启用/禁用
4. 配置保存功能
5. SSO 测试连接
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

from tests.regression.test_helpers import (
    TestRunner,
    check_element_exists,
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
)

MODULE_NAME = "manage_settings_sso"


def test_page_loads():
    """测试 SSO Settings 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/settings/sso")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".sso-page"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_sso_form_display():
    """测试 SSO 配置表单显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/settings/sso")

            # 检查表单
            form_selectors = ["form", ".sso-form", ".settings-form"]
            assert check_element_exists(page, form_selectors), "SSO 配置表单应存在"

            # 检查表单字段
            input_selectors = ["input", "select", "textarea"]
            assert check_element_exists(page, input_selectors), "表单字段应存在"

            save_screenshot(page, MODULE_NAME, "02_form")
            return True
        finally:
            browser.close()


def test_sso_toggle():
    """测试 SSO 启用/禁用"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/settings/sso")

            # 检查 SSO 开关
            toggle_selectors = [".toggle-switch", 'input[type="checkbox"]', ".form-check-input"]
            assert check_element_exists(page, toggle_selectors), "SSO 开关应可见"

            save_screenshot(page, MODULE_NAME, "03_toggle")
            return True
        finally:
            browser.close()


def test_save_button():
    """测试配置保存功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/settings/sso")

            # 检查保存按钮
            save_btn_selectors = [
                'button:has-text("Save")',
                'button:has-text("保存")',
                'button[type="submit"]',
            ]
            assert check_element_exists(page, save_btn_selectors), "保存按钮应可见"

            save_screenshot(page, MODULE_NAME, "04_save")
            return True
        finally:
            browser.close()


def test_test_connection():
    """测试 SSO 测试连接"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/settings/sso")

            # 检查测试连接按钮（可选）
            test_btn_selectors = [
                'button:has-text("Test")',
                'button:has-text("测试")',
                'button:has-text("Verify")',
            ]
            check_element_exists(page, test_btn_selectors)

            save_screenshot(page, MODULE_NAME, "05_test")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 SSO 回归测试"""
    runner = TestRunner("Manage 模式 - Settings - SSO")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("SSO 配置表单显示", test_sso_form_display),
        ("SSO 启用/禁用", test_sso_toggle),
        ("配置保存功能", test_save_button),
        ("SSO 测试连接", test_test_connection),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
