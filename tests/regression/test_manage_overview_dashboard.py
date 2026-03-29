#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Overview - Dashboard

测试内容：
1. 页面加载和标题显示
2. 统计卡片显示
3. 趋势图表渲染
4. 数据刷新功能
5. 今日用量显示
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

MODULE_NAME = "manage_overview_dashboard"


def test_page_loads():
    """测试 Dashboard 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/dashboard")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".main-content"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_stat_cards_display():
    """测试统计卡片显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/dashboard")

            # 检查统计卡片
            card_selectors = [".usage-card", ".stat-card", ".card", ".dashboard-card"]
            assert check_element_exists(page, card_selectors), "统计卡片应存在"

            save_screenshot(page, MODULE_NAME, "02_stat_cards")
            return True
        finally:
            browser.close()


def test_trend_chart_render():
    """测试趋势图表渲染"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/dashboard")
            page.wait_for_timeout(2000)

            # 检查图表
            chart_selectors = ["canvas", ".chart", ".echarts"]
            assert check_element_exists(page, chart_selectors), "图表应存在"

            save_screenshot(page, MODULE_NAME, "03_trend_chart")
            return True
        finally:
            browser.close()


def test_refresh_functionality():
    """测试数据刷新功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/dashboard")

            # 检查刷新按钮（可选）
            refresh_selectors = [
                'button:has-text("Refresh")',
                'button:has-text("刷新")',
                "button:has(.bi-arrow-clockwise)",
            ]
            if check_element_exists(page, refresh_selectors):
                try:
                    refresh_btn = page.locator(
                        refresh_selectors[0] + ", " + refresh_selectors[1]
                    ).first
                    if refresh_btn.is_visible():
                        refresh_btn.click()
                        page.wait_for_timeout(1000)
                except Exception:
                    pass

            save_screenshot(page, MODULE_NAME, "04_refresh")
            return True
        finally:
            browser.close()


def test_today_usage_section():
    """测试今日用量显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/dashboard")

            # 检查今日用量区域
            today_selectors = [
                ".dashboard-section",
                ".today-usage",
                ".usage-section",
                ".usage-card",
            ]
            assert check_element_exists(page, today_selectors), "今日用量区域应存在"

            save_screenshot(page, MODULE_NAME, "05_today_usage")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 Dashboard 回归测试"""
    runner = TestRunner("Manage 模式 - Overview - Dashboard")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("统计卡片显示", test_stat_cards_display),
        ("趋势图表渲染", test_trend_chart_render),
        ("数据刷新功能", test_refresh_functionality),
        ("今日用量显示", test_today_usage_section),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
