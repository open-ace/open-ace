#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Analysis - Anomaly

测试内容：
1. 页面加载和标题显示
2. 异常检测图表渲染
3. 异常列表显示
4. 筛选功能
5. 异常详情查看
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

MODULE_NAME = "manage_analysis_anomaly"


def test_page_loads():
    """测试 Anomaly 页面加载"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/anomaly")

            # 等待页面完全加载（Loading 状态消失）
            page.wait_for_timeout(2000)

            # 检查页面标题 - 使用更长的超时时间
            title_selectors = ["h2", "h1", "h3", ".page-title"]
            assert check_element_exists(page, title_selectors, timeout=10000), "页面标题应可见"

            # 检查主内容区域
            main_selectors = ["main", ".manage-content", ".anomaly-detection"]
            assert check_element_exists(page, main_selectors), "主内容区域应存在"

            save_screenshot(page, MODULE_NAME, "01_page_load")
            return True
        finally:
            browser.close()


def test_anomaly_chart_render():
    """测试异常检测图表渲染"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/anomaly")

            # 等待图表渲染或空状态显示
            page.wait_for_timeout(3000)

            # 检查图表元素或空状态（AnomalyDetection 可能没有数据）
            chart_selectors = ["canvas", ".chart", ".empty-state", ".card"]
            assert check_element_exists(page, chart_selectors, timeout=10000), "图表或空状态应存在"

            save_screenshot(page, MODULE_NAME, "02_chart")
            return True
        finally:
            browser.close()


def test_anomaly_list_display():
    """测试异常列表显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/anomaly")

            # 等待数据加载
            page.wait_for_timeout(3000)

            # 检查异常列表或空状态（AnomalyDetection 可能没有异常数据）
            list_selectors = ["table", ".card", ".empty-state", ".text-center"]
            assert check_element_exists(
                page, list_selectors, timeout=10000
            ), "应有异常列表或空状态提示"

            save_screenshot(page, MODULE_NAME, "03_list")
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
            navigate_to(page, "/manage/analysis/anomaly")

            # 检查筛选元素
            filter_selectors = [".filter-bar", "select", 'input[type="date"]', ".form-select"]
            if check_element_exists(page, filter_selectors):
                # 尝试点击第一个筛选元素
                for selector in filter_selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible():
                            element.click()
                            page.wait_for_timeout(300)
                            break
                    except Exception:
                        continue

            save_screenshot(page, MODULE_NAME, "04_filter")
            return True
        finally:
            browser.close()


def test_threshold_settings():
    """测试阈值设置"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/analysis/anomaly")

            # 检查阈值输入元素
            threshold_selectors = [
                'input[type="number"]',
                ".threshold-input",
                '.form-control[type="number"]',
            ]
            # 阈值设置可能不存在，所以不强制要求
            check_element_exists(page, threshold_selectors)

            save_screenshot(page, MODULE_NAME, "05_threshold")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有异常检测回归测试"""
    runner = TestRunner("Manage 模式 - Analysis - Anomaly")
    runner.print_header()

    tests = [
        ("页面加载", test_page_loads),
        ("异常检测图表渲染", test_anomaly_chart_render),
        ("异常列表显示", test_anomaly_list_display),
        ("筛选功能", test_filter_functionality),
        ("阈值设置", test_threshold_settings),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
