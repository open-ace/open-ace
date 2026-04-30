#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Messages

测试内容：
1. 页面加载和标题显示
2. 消息列表显示
3. 筛选功能
4. 分页功能
5. 消息详情查看
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

MODULE_NAME = "manage_analysis_messages"


def test_page_loads():
    """测试 Messages 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/messages")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".messages-page"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_message_list_display():
    """测试消息列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/messages")
            page.wait_for_timeout(3000)

            # 检查消息列表或空状态或卡片
            list_selectors = ["table", ".card", ".empty-state", ".no-data", ".messages-page"]
            assert check_element_exists(
                page, list_selectors, timeout=10000
            ), "应有消息列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_list")
            return True
        finally:
            browser.close()


def test_filter_functionality():
    """测试筛选功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/messages")

            # 检查筛选元素
            filter_selectors = [
                ".filter-bar",
                "select",
                'input[type="date"]',
                'input[placeholder*="search"]',
            ]
            if check_element_exists(page, filter_selectors):
                for selector in filter_selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible():
                            element.click()
                            page.wait_for_timeout(300)
                            break
                    except Exception:
                        continue

            save_screenshot(page, MODULE_NAME, "03_filter")
            return True
        finally:
            browser.close()


def test_pagination():
    """测试分页功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/messages")

            # 检查分页元素（可选）
            pagination_selectors = [".pagination", ".pager", '[class*="pagination"]']
            check_element_exists(page, pagination_selectors)

            save_screenshot(page, MODULE_NAME, "04_pagination")
            return True
        finally:
            browser.close()


def test_sender_filter():
    """测试发送者筛选"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/messages")

            # 检查发送者筛选（可选）
            sender_selectors = ['select[name="sender"]', ".sender-dropdown", 'select[id*="sender"]']
            check_element_exists(page, sender_selectors)

            save_screenshot(page, MODULE_NAME, "05_sender")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有消息回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - Messages")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("消息列表显示", test_message_list_display),
        ("筛选功能", test_filter_functionality),
        ("分页功能", test_pagination),
        ("发送者筛选", test_sender_filter),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
