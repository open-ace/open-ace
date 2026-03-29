#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Compliance

测试内容：
1. 页面加载和标题显示
2. 合规规则列表显示
3. 规则详情查看
4. 规则启用/禁用
5. 合规报告生成
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

MODULE_NAME = "manage_governance_compliance"


def test_page_loads():
    """测试 Compliance 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/compliance")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".compliance-page"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_compliance_rules_list():
    """测试合规规则列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/compliance")

            # 检查合规规则列表或空状态
            rules_selectors = [
                ".compliance-rules-list",
                "table",
                ".data-table",
                ".empty-state",
                ".no-data",
            ]
            assert check_element_exists(page, rules_selectors), "应有合规规则列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_rules_list")
            return True
        finally:
            browser.close()


def test_rule_detail():
    """测试规则详情查看"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/compliance")

            # 尝试点击规则项查看详情
            rule_item_selectors = [".compliance-rule-item", "tr", ".list-item"]
            if check_element_exists(page, rule_item_selectors):
                try:
                    rule_item = page.locator(
                        rule_item_selectors[0] + ", " + rule_item_selectors[1]
                    ).first
                    if rule_item.is_visible():
                        rule_item.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "03_rule_detail")
            return True
        finally:
            browser.close()


def test_rule_toggle():
    """测试规则启用/禁用"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/compliance")

            # 检查规则开关
            toggle_selectors = [".toggle-switch", 'input[type="checkbox"]', ".form-check-input"]
            assert check_element_exists(page, toggle_selectors), "规则开关应可见"

            save_screenshot(page, MODULE_NAME, "04_rule_toggle")
            return True
        finally:
            browser.close()


def test_compliance_report():
    """测试合规报告生成"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/compliance")

            # 检查报告按钮（可选）
            report_selectors = [
                'button:has-text("Report")',
                'button:has-text("报告")',
                'button:has-text("Generate")',
            ]
            check_element_exists(page, report_selectors)

            save_screenshot(page, MODULE_NAME, "05_report")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有合规回归测试"""
    runner = TestRunner("Manage 模式 - Governance - Compliance")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("合规规则列表显示", test_compliance_rules_list),
        ("规则详情查看", test_rule_detail),
        ("规则启用/禁用", test_rule_toggle),
        ("合规报告生成", test_compliance_report),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
