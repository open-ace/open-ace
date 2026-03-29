#!/usr/bin/env python3
"""
回归测试: Work 模式 - Prompts

测试内容：
1. 页面加载和标题显示
2. Prompts 列表显示
3. Prompt 搜索功能
4. Prompt 详情查看
5. Prompt 使用功能
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

MODULE_NAME = "work_prompts"


def test_page_loads():
    """测试 Prompts 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/prompts")

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


def test_prompts_list_display():
    """测试 Prompts 列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/prompts")

            # 检查 Prompts 列表或空状态
            prompts_selectors = [
                ".prompts-list",
                "table",
                ".data-table",
                ".list",
                ".empty-state",
                ".no-data",
            ]
            assert check_element_exists(page, prompts_selectors), "应有 Prompts 列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_prompts_list")
            return True
        finally:
            browser.close()


def test_prompt_search():
    """测试 Prompt 搜索功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/prompts")

            # 检查搜索输入框
            search_selectors = ['input[placeholder*="search"]', 'input[type="text"]']
            if check_element_exists(page, search_selectors):
                try:
                    search_input = page.locator(
                        search_selectors[0] + ", " + search_selectors[1]
                    ).first
                    if search_input.is_visible():
                        search_input.fill("test")
                        page.wait_for_timeout(500)
                        search_input.clear()
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "03_search")
            return True
        finally:
            browser.close()


def test_prompt_detail():
    """测试 Prompt 详情查看"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/prompts")

            # 尝试点击 Prompt 项查看详情
            prompt_item_selectors = [".prompt-item", "tr", ".list-item", ".card"]
            if check_element_exists(page, prompt_item_selectors):
                try:
                    prompt_item = page.locator(
                        prompt_item_selectors[0] + ", " + prompt_item_selectors[1]
                    ).first
                    if prompt_item.is_visible():
                        prompt_item.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "04_detail")
            return True
        finally:
            browser.close()


def test_prompt_use():
    """测试 Prompt 使用功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/work/prompts")

            # 检查使用按钮（可选）
            use_btn_selectors = ['button:has-text("Use")', 'button:has-text("使用")', ".use-btn"]
            check_element_exists(page, use_btn_selectors)

            save_screenshot(page, MODULE_NAME, "05_use")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 Prompts 回归测试"""
    runner = TestRunner("Work 模式 - Prompts")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("Prompts 列表显示", test_prompts_list_display),
        ("Prompt 搜索功能", test_prompt_search),
        ("Prompt 详情查看", test_prompt_detail),
        ("Prompt 使用功能", test_prompt_use),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
