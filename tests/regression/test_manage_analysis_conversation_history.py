#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Conversation History

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 会话详情查看
4. 搜索筛选功能
5. 会话导出功能
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

MODULE_NAME = "manage_analysis_conversation_history"


def test_page_loads():
    """测试 Conversation History 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/conversation-history")

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


def test_conversation_list_display():
    """测试会话列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/conversation-history")
            page.wait_for_timeout(3000)

            # 检查会话列表或空状态
            list_selectors = ["table", ".card", ".empty-state", ".no-data"]
            assert check_element_exists(
                page, list_selectors, timeout=10000
            ), "应有会话列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_list")
            return True
        finally:
            browser.close()


def test_conversation_detail():
    """测试会话详情查看"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/conversation-history")
            page.wait_for_timeout(3000)

            # 点击第一个会话查看详情
            conv_item = page.locator("tr, .list-item").first

            if conv_item.count() > 0 and conv_item.is_visible():
                try:
                    conv_item.click()
                    page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "03_detail")
            return True
        finally:
            browser.close()


def test_search_filter():
    """测试搜索筛选功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/conversation-history")
            page.wait_for_timeout(3000)

            # 检查筛选元素
            filter_selectors = ["select", 'input[type="date"]', ".form-control"]
            check_element_exists(page, filter_selectors)

            save_screenshot(page, MODULE_NAME, "04_search")
            return True
        finally:
            browser.close()


def test_export_function():
    """测试会话导出功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/conversation-history")
            page.wait_for_timeout(3000)

            # 检查导出按钮（可选）
            export_selectors = [
                'button:has-text("Export")',
                'button:has-text("导出")',
                "button:has(.bi-download)",
            ]
            check_element_exists(page, export_selectors)

            save_screenshot(page, MODULE_NAME, "05_export")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有会话历史回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - Conversation History")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("会话列表显示", test_conversation_list_display),
        ("会话详情查看", test_conversation_detail),
        ("搜索筛选功能", test_search_filter),
        ("会话导出功能", test_export_function),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
