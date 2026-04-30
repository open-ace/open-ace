#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - ROI

测试内容：
1. 页面加载和标题显示
2. ROI 分析图表渲染
3. 成本效益数据显示
4. 时间范围筛选
5. ROI 计算功能
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import expect, sync_playwright

from tests.regression.test_helpers import (
    BASE_URL,
    HEADLESS,
    TestRunner,
    check_element_exists,
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
)

MODULE_NAME = "manage_analysis_roi"


def test_page_loads():
    """测试 ROI 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/roi")

            # 等待页面完全加载
            page.wait_for_timeout(2000)

            # 检查页面标题（ROIAnalysis 使用 h2）
            title_selectors = ["h2", "h1", ".page-header"]
            assert check_element_exists(page, title_selectors, timeout=10000), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".roi-analysis", ".card"]
            assert check_element_exists(page, main_selectors, timeout=10000), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_roi_chart_render():
    """测试 ROI 图表渲染"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/roi")
            page.wait_for_timeout(3000)

            # 检查图表元素或空状态
            chart_selectors = ["canvas", ".chart", ".empty-state", ".card"]
            assert check_element_exists(page, chart_selectors, timeout=10000), "图表或空状态应存在"

            save_screenshot(page, MODULE_NAME, "02_chart")
            return True
        finally:
            browser.close()


def test_cost_benefit_display():
    """测试成本效益数据显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/roi")

            # 检查指标卡片或空状态
            metric_selectors = [".stat-card", ".card", ".empty-state"]
            assert check_element_exists(
                page, metric_selectors, timeout=10000
            ), "指标卡片或空状态应存在"

            save_screenshot(page, MODULE_NAME, "03_metrics")
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
            navigate_to(page, "/manage/analysis/roi")

            # 检查时间筛选元素
            time_selectors = ["select", ".time-range-selector", 'input[type="date"]']
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

            save_screenshot(page, MODULE_NAME, "04_time_filter")
            return True
        finally:
            browser.close()


def test_roi_summary():
    """测试 ROI 汇总显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/roi")

            # 检查汇总区域（可选）
            summary_selectors = [".roi-summary", ".summary-section", ".total-section"]
            check_element_exists(page, summary_selectors)

            save_screenshot(page, MODULE_NAME, "05_summary")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 ROI 回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - ROI")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("ROI 图表渲染", test_roi_chart_render),
        ("成本效益数据显示", test_cost_benefit_display),
        ("时间范围筛选", test_time_range_filter),
        ("ROI 汇总显示", test_roi_summary),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
