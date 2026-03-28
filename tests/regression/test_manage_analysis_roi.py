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

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'regression')


def ensure_screenshot_dir():
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f'manage_analysis_roi_{name}.png')
    page.screenshot(path=path)
    return path


def login(page):
    page.goto(f'{BASE_URL}/login')
    page.wait_for_load_state('networkidle')
    page.fill('#username', USERNAME)
    page.fill('#password', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: '/login' not in url, timeout=10000)


def test_page_loads():
    """测试 ROI 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/roi')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_roi_chart_render():
    """测试 ROI 图表渲染"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/roi')
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)

            charts = page.locator('canvas, .chart, .echarts')
            assert charts.count() > 0, "图表应存在"

            save_screenshot(page, '02_chart')
            return True
        finally:
            browser.close()


def test_cost_benefit_display():
    """测试成本效益数据显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/roi')
            page.wait_for_load_state('networkidle')

            metrics = page.locator('.metric-card, .stat-card, .roi-card, .card')
            assert metrics.count() > 0, "指标卡片应存在"

            save_screenshot(page, '03_metrics')
            return True
        finally:
            browser.close()


def test_time_range_filter():
    """测试时间范围筛选"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/roi')
            page.wait_for_load_state('networkidle')

            time_filter = page.locator('select, .time-range-selector, input[type="date"]')

            if time_filter.count() > 0:
                time_filter.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '04_time_filter')
            return True
        finally:
            browser.close()


def test_roi_summary():
    """测试 ROI 汇总显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/roi')
            page.wait_for_load_state('networkidle')

            summary = page.locator('.roi-summary, .summary-section, .total-section')

            if summary.count() > 0:
                assert summary.first.is_visible(), "ROI 汇应可见"

            save_screenshot(page, '05_summary')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('ROI 图表渲染', test_roi_chart_render),
        ('成本效益数据显示', test_cost_benefit_display),
        ('时间范围筛选', test_time_range_filter),
        ('ROI 汇总显示', test_roi_summary),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Analysis - ROI 回归测试")
    print("=" * 60)

    for name, test_func in tests:
        try:
            test_func()
            results.append((name, 'PASS', None))
            print(f"  ✓ {name}")
        except Exception as e:
            results.append((name, 'FAIL', str(e)))
            print(f"  ✗ {name}: {e}")

    print("\n" + "-" * 60)
    passed = sum(1 for r in results if r[1] == 'PASS')
    total = len(results)
    print(f"结果: {passed}/{total} 通过")
    print("-" * 60)

    return results


if __name__ == '__main__':
    run_all_tests()