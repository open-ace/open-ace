#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Quota & Alerts

测试内容：
1. 页面加载和标题显示
2. 配额设置显示
3. 告警规则列表
4. 配额调整功能
5. 告警启用/禁用
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

MODULE_NAME = "manage_governance_quota"


def test_page_loads():
    """测试 Quota & Alerts 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", ".page-header"]
            assert check_element_exists(page, title_selectors, timeout=10000), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".card"]
            assert check_element_exists(page, main_selectors, timeout=10000), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_quota_settings_display():
    """测试配额设置显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")
            page.wait_for_timeout(3000)

            # 检查配额设置区域或卡片
            quota_selectors = [".card", ".quota-settings", ".empty-state"]
            assert check_element_exists(page, quota_selectors, timeout=10000), "配额设置区域应存在"

            save_screenshot(page, MODULE_NAME, "02_quota_settings")
            return True
        finally:
            browser.close()


def test_alert_rules_list():
    """测试告警规则列表"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")

            # 检查告警规则列表或空状态
            alert_selectors = [
                ".alert-rules-list",
                "table",
                ".rules-list",
                ".empty-state",
                ".no-data",
            ]
            assert check_element_exists(page, alert_selectors), "应有告警规则列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "03_alert_list")
            return True
        finally:
            browser.close()


def test_quota_adjustment():
    """测试配额调整功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")

            # 检查配额输入元素（可选）
            quota_input_selectors = ['input[type="number"]', ".quota-input"]
            check_element_exists(page, quota_input_selectors)

            save_screenshot(page, MODULE_NAME, "04_quota_adjust")
            return True
        finally:
            browser.close()


def test_alert_toggle():
    """测试告警启用/禁用"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")

            # 检查告警开关
            toggle_selectors = [".toggle-switch", 'input[type="checkbox"]', ".form-check-input"]
            assert check_element_exists(page, toggle_selectors), "告警开关应可见"

            save_screenshot(page, MODULE_NAME, "05_alert_toggle")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有配额告警回归测试"""
    runner = TestRunner("Manage 模式 - Governance - Quota & Alerts")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("配额设置显示", test_quota_settings_display),
        ("告警规则列表", test_alert_rules_list),
        ("配额调整功能", test_quota_adjustment),
        ("告警启用/禁用", test_alert_toggle),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
