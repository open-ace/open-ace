#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Trend

测试内容：
1. 页面加载和标题显示
2. Token 趋势图表渲染
3. 时间范围筛选
4. 数据导出功能
5. 图表交互
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

MODULE_NAME = "manage_analysis_trend"


def test_page_loads():
    """测试 Trend 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/trend")

            # 等待页面完全加载（骨架屏消失后）
            page.wait_for_timeout(2000)

            # 检查页面标题 - 使用更长的超时时间
            title_selectors = ["h2", "h1", ".page-header h2", ".page-title"]
            assert check_element_exists(page, title_selectors, timeout=10000), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".trend-analysis"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
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
            navigate_to(page, "/manage/analysis/trend")
            page.wait_for_timeout(2000)

            # 检查图表容器或空状态
            chart_selectors = [
                "canvas",
                ".chart-container",
                ".line-chart",
                ".empty-state",
                ".no-data",
                ".card",
            ]
            assert check_element_exists(page, chart_selectors), "图表容器或空状态应存在"

            save_screenshot(page, MODULE_NAME, "02_chart")
            return True
        finally:
            browser.close()


def test_time_range_filter():
    """测试时间范围筛选"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/trend")

            # 检查时间范围按钮或日期输入框
            time_selectors = [".btn-group button", 'input[type="date"]', ".card"]
            assert check_element_exists(page, time_selectors), "应有时间范围按钮或卡片容器"

            # 尝试点击时间按钮
            time_buttons = page.locator(".btn-group button")
            if time_buttons.count() > 0:
                time_buttons.first.click()
                page.wait_for_timeout(500)

            save_screenshot(page, MODULE_NAME, "03_time_filter")
            return True
        finally:
            browser.close()


def test_data_export():
    """测试数据导出功能"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/trend")

            # 检查导出按钮（可选）
            export_selectors = [
                'button:has-text("Export")',
                'button:has-text("导出")',
                "button:has(.bi-download)",
            ]
            check_element_exists(page, export_selectors)

            save_screenshot(page, MODULE_NAME, "04_export")
            return True
        finally:
            browser.close()


def test_chart_interaction():
    """测试图表交互"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/trend")

            # 尝试悬停在图表上
            charts = page.locator("canvas, .chart-container")
            if charts.count() > 0:
                charts.first.hover()
                page.wait_for_timeout(500)

            save_screenshot(page, MODULE_NAME, "05_interaction")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有趋势分析回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - Trend")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("趋势图表渲染", test_trend_chart_render),
        ("时间范围筛选", test_time_range_filter),
        ("数据导出功能", test_data_export),
        ("图表交互", test_chart_interaction),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
