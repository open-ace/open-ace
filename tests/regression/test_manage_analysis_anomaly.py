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
    path = os.path.join(SCREENSHOT_DIR, f'manage_analysis_anomaly_{name}.png')
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
    """测试 Anomaly 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/anomaly')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_anomaly_chart_render():
    """测试异常检测图表渲染"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/anomaly')
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)

            charts = page.locator('canvas, .chart, .echarts')
            assert charts.count() > 0, "图表应存在"

            save_screenshot(page, '02_chart')
            return True
        finally:
            browser.close()


def test_anomaly_list_display():
    """测试异常列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/anomaly')
            page.wait_for_load_state('networkidle')

            anomaly_list = page.locator('.anomaly-list, table, .data-table, .list')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = anomaly_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有异常列表或空状态提示"

            save_screenshot(page, '03_list')
            return True
        finally:
            browser.close()


def test_filter_functionality():
    """测试筛选功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/anomaly')
            page.wait_for_load_state('networkidle')

            filters = page.locator('.filter-bar, select, input[type="date"]')

            if filters.count() > 0:
                filters.first.click()
                page.wait_for_timeout(300)

            save_screenshot(page, '04_filter')
            return True
        finally:
            browser.close()


def test_threshold_settings():
    """测试阈值设置"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/analysis/anomaly')
            page.wait_for_load_state('networkidle')

            threshold_input = page.locator('input[type="number"], .threshold-input, input:has-text("threshold")')

            if threshold_input.count() > 0:
                assert threshold_input.first.is_visible(), "阈值输入应可见"

            save_screenshot(page, '05_threshold')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('异常检测图表渲染', test_anomaly_chart_render),
        ('异常列表显示', test_anomaly_list_display),
        ('筛选功能', test_filter_functionality),
        ('阈值设置', test_threshold_settings),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Analysis - Anomaly 回归测试")
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