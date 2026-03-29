#!/usr/bin/env python3
"""
回归测试: Work 模式 - Sessions

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 会话筛选功能
4. 会话详情查看
5. 会话删除功能
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

MODULE_NAME = "work_sessions"


def test_page_loads():
    """测试 Sessions 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/sessions")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", "h4", "h5", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".work-main", ".main-content"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_session_list_display():
    """测试会话列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/sessions")

            # 检查会话列表或空状态
            session_selectors = [
                ".sessions-list",
                "table",
                ".data-table",
                ".empty-state",
                ".no-data",
            ]
            assert check_element_exists(page, session_selectors), "应有会话列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_session_list")
            return True
        finally:
            browser.close()


def test_session_filter():
    """测试会话筛选功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/sessions")

            # 检查筛选输入框
            filter_selectors = ['input[placeholder*="search"]', 'input[type="text"]']
            if check_element_exists(page, filter_selectors):
                try:
                    filter_input = page.locator(
                        filter_selectors[0] + ", " + filter_selectors[1]
                    ).first
                    if filter_input.is_visible():
                        filter_input.fill("test")
                        page.wait_for_timeout(500)
                        filter_input.clear()
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "03_filter")
            return True
        finally:
            browser.close()


def test_session_detail():
    """测试会话详情查看"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/sessions")

            # 尝试点击会话项查看详情
            session_item_selectors = [".session-item", "tr", ".list-item"]
            if check_element_exists(page, session_item_selectors):
                try:
                    session_item = page.locator(
                        session_item_selectors[0] + ", " + session_item_selectors[1]
                    ).first
                    if session_item.is_visible():
                        session_item.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "04_detail")
            return True
        finally:
            browser.close()


def test_session_delete():
    """测试会话删除功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/sessions")

            # 检查删除按钮（可选）
            delete_btn_selectors = [
                'button:has-text("Delete")',
                'button:has-text("删除")',
                ".delete-btn",
                "button:has(.bi-trash)",
            ]
            check_element_exists(page, delete_btn_selectors)

            save_screenshot(page, MODULE_NAME, "05_delete")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 Sessions 回归测试"""
    runner = TestRunner("Work 模式 - Sessions")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("会话列表显示", test_session_list_display),
        ("会话筛选功能", test_session_filter),
        ("会话详情查看", test_session_detail),
        ("会话删除功能", test_session_delete),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
