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

# 配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'regression')


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    """保存截图"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f'manage_overview_dashboard_{name}.png')
    page.screenshot(path=path)
    return path


def login(page):
    """登录辅助函数"""
    page.goto(f'{BASE_URL}/login')
    page.wait_for_load_state('networkidle')
    page.fill('#username', USERNAME)
    page.fill('#password', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: '/login' not in url, timeout=10000)


def test_page_loads():
    """测试 Dashboard 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/dashboard')
            page.wait_for_load_state('networkidle')

            # 验证页面标题
            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            # 验证主内容区域
            main_content = page.locator('main, .manage-content, .main-content')
            assert main_content.count() > 0, "主内容区域应存在"

            # 验证无错误提示
            error_alert = page.locator('.alert-danger, .error-message')
            assert error_alert.count() == 0, "不应有错误提示"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_stat_cards_display():
    """测试统计卡片显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/dashboard')
            page.wait_for_load_state('networkidle')

            # 验证统计卡片存在
            stat_cards = page.locator('.usage-card, .stat-card, .card, .dashboard-card')
            assert stat_cards.count() > 0, "统计卡片应存在"

            # 验证卡片内容
            first_card = stat_cards.first()
            assert first_card.is_visible(), "第一个卡片应可见"

            save_screenshot(page, '02_stat_cards')
            return True
        finally:
            browser.close()


def test_trend_chart_render():
    """测试趋势图表渲染"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/dashboard')
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)  # 等待图表渲染

            # 验证图表存在
            charts = page.locator('canvas, .chart, .echarts')
            assert charts.count() > 0, "图表应存在"

            # 验证图表可见
            first_chart = charts.first()
            assert first_chart.is_visible(), "图表应可见"

            save_screenshot(page, '03_trend_chart')
            return True
        finally:
            browser.close()


def test_refresh_functionality():
    """测试数据刷新功能"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/dashboard')
            page.wait_for_load_state('networkidle')

            # 查找刷新按钮
            refresh_btn = page.locator('button:has-text("Refresh"), button:has-text("刷新"), button:has(.bi-arrow-clockwise)')

            if refresh_btn.count() > 0:
                refresh_btn.first.click()
                page.wait_for_load_state('networkidle')
                page.wait_for_timeout(1000)

                # 验证刷新后页面正常
                main_content = page.locator('main, .manage-content')
                assert main_content.count() > 0, "刷新后页面应正常"

            save_screenshot(page, '04_refresh')
            return True
        finally:
            browser.close()


def test_today_usage_section():
    """测试今日用量显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/dashboard')
            page.wait_for_load_state('networkidle')

            # 验证今日用量区域
            today_section = page.locator('.dashboard-section, .today-usage, .usage-section')
            assert today_section.count() > 0, "今日用量区域应存在"

            # 验证用量数据
            usage_cards = page.locator('.usage-card')
            assert usage_cards.count() > 0, "用量卡片应存在"

            save_screenshot(page, '05_today_usage')
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有 Dashboard 回归测试"""
    tests = [
        ('页面加载', test_page_loads),
        ('统计卡片显示', test_stat_cards_display),
        ('趋势图表渲染', test_trend_chart_render),
        ('数据刷新功能', test_refresh_functionality),
        ('今日用量显示', test_today_usage_section),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Overview - Dashboard 回归测试")
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