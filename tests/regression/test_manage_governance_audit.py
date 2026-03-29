#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Audit Center

测试内容：
1. 页面加载和标题显示
2. 审计日志列表显示
3. 时间筛选功能
4. 用户筛选功能
5. 日志详情查看
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

MODULE_NAME = "manage_governance_audit"


def test_page_loads():
    """测试 Audit Center 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/audit")

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


def test_audit_log_list():
    """测试审计日志列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/audit")
            page.wait_for_timeout(3000)

            # 检查日志列表或空状态
            list_selectors = ["table", ".card", ".empty-state", ".no-data"]
            assert check_element_exists(
                page, list_selectors, timeout=10000
            ), "应有审计日志列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "02_list")
            return True
        finally:
            browser.close()


def test_time_filter():
    """测试时间筛选功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/audit")

            # 检查时间筛选元素
            time_selectors = ['input[type="date"]', ".date-picker", 'select[name*="time"]']
            if check_element_exists(page, time_selectors):
                for selector in time_selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible():
                            element.click()
                            page.wait_for_timeout(300)
                            break
                    except Exception:
                        continue

            save_screenshot(page, MODULE_NAME, "03_time_filter")
            return True
        finally:
            browser.close()


def test_user_filter():
    """测试用户筛选功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/audit")

            # 检查用户筛选元素（可选）
            user_selectors = ['select[name*="user"]', ".user-filter"]
            check_element_exists(page, user_selectors)

            save_screenshot(page, MODULE_NAME, "04_user_filter")
            return True
        finally:
            browser.close()


def test_log_detail():
    """测试日志详情查看"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/audit")

            # 尝试点击日志项查看详情
            log_item_selectors = [".audit-log-item", "tr", ".list-item"]
            if check_element_exists(page, log_item_selectors):
                try:
                    log_item = page.locator(
                        log_item_selectors[0] + ", " + log_item_selectors[1]
                    ).first
                    if log_item.is_visible():
                        log_item.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "05_detail")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有审计中心回归测试"""
    runner = TestRunner("Manage 模式 - Governance - Audit Center")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("审计日志列表显示", test_audit_log_list),
        ("时间筛选功能", test_time_filter),
        ("用户筛选功能", test_user_filter),
        ("日志详情查看", test_log_detail),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
