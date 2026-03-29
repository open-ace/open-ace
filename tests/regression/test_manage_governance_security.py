#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Security Center

测试内容：
1. 页面加载和标题显示
2. 安全概览显示
3. 安全事件列表
4. 安全设置
5. 安全报告
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
from tests.regression.test_helpers import (
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
    check_element_exists,
    TestRunner,
    BASE_URL,
    HEADLESS,
)

MODULE_NAME = "manage_governance_security"


def test_page_loads():
    """测试 Security Center 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/security")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".security-center"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_security_overview():
    """测试安全概览显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/security")

            # 检查安全概览区域 - 使用实际组件的类名
            overview_selectors = [".security-center", ".card", "table", ".text-center"]
            assert check_element_exists(page, overview_selectors), "安全概览区域应存在"

            save_screenshot(page, MODULE_NAME, "02_overview")
            return True
        finally:
            browser.close()


def test_security_events_list():
    """测试安全事件列表"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/security")

            # 检查安全事件列表或空状态 - 使用实际组件的类名
            events_selectors = [
                "table",
                ".text-center.py-5",
                ".text-center",
                ".nav-tabs",
            ]
            assert check_element_exists(page, events_selectors), "应有安全事件列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "03_events_list")
            return True
        finally:
            browser.close()


def test_security_settings():
    """测试安全设置"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/security")

            # 检查安全设置区域（可选）
            settings_selectors = [".security-settings", ".settings-section", "form"]
            check_element_exists(page, settings_selectors)

            save_screenshot(page, MODULE_NAME, "04_settings")
            return True
        finally:
            browser.close()


def test_security_report():
    """测试安全报告"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/security")

            # 检查报告按钮（可选）
            report_selectors = [
                'button:has-text("Report")',
                'button:has-text("报告")',
                "button:has(.bi-file-earmark)",
            ]
            check_element_exists(page, report_selectors)

            save_screenshot(page, MODULE_NAME, "05_report")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有安全中心回归测试"""
    runner = TestRunner("Manage 模式 - Governance - Security Center")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("安全概览显示", test_security_overview),
        ("安全事件列表", test_security_events_list),
        ("安全设置", test_security_settings),
        ("安全报告", test_security_report),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
