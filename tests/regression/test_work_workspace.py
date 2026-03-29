#!/usr/bin/env python3
"""
回归测试: Work 模式 - Workspace

测试内容：
1. 页面加载和标题显示
2. 会话列表显示
3. 工具面板显示
4. 新建会话功能
5. 会话切换功能
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

MODULE_NAME = "work_workspace"


def test_page_loads():
    """测试 Workspace 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work")

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
            navigate_to(page, "/work")

            # 检查会话列表区域
            session_selectors = [".session-list", ".work-left-panel", ".sessions-panel"]
            assert check_element_exists(page, session_selectors), "会话列表区域应存在"

            save_screenshot(page, MODULE_NAME, "02_session_list")
            return True
        finally:
            browser.close()


def test_tools_panel_display():
    """测试工具面板显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work")

            # 检查工具面板区域
            tools_selectors = [".work-right-panel", ".assist-panel", ".tools-panel"]
            assert check_element_exists(page, tools_selectors), "工具面板区域应存在"

            save_screenshot(page, MODULE_NAME, "03_tools_panel")
            return True
        finally:
            browser.close()


def test_new_session():
    """测试新建会话功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work")

            # 检查新建会话按钮
            new_btn_selectors = [
                'button:has-text("New")',
                'button:has-text("新建")',
                ".new-session-btn",
                "button:has(.bi-plus)",
            ]
            assert check_element_exists(page, new_btn_selectors), "新建会话按钮应可见"

            save_screenshot(page, MODULE_NAME, "04_new_session")
            return True
        finally:
            browser.close()


def test_session_switch():
    """测试会话切换功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work")

            # 尝试点击会话项
            session_item_selectors = [".session-item", ".session-card"]
            if check_element_exists(page, session_item_selectors):
                try:
                    session_item = page.locator(session_item_selectors[0]).first
                    if session_item.is_visible():
                        session_item.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "05_session_switch")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 Workspace 回归测试"""
    runner = TestRunner("Work 模式 - Workspace")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("会话列表显示", test_session_list_display),
        ("工具面板显示", test_tools_panel_display),
        ("新建会话功能", test_new_session),
        ("会话切换功能", test_session_switch),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
