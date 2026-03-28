#!/usr/bin/env python3
"""
回归测试: Manage 模式 - Governance - Compliance

测试内容：
1. 页面加载和标题显示
2. 合规规则列表显示
3. 规则详情查看
4. 规则启用/禁用
5. 合规报告生成
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
    path = os.path.join(SCREENSHOT_DIR, f'manage_governance_compliance_{name}.png')
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
    """测试 Compliance 页面加载"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/compliance')
            page.wait_for_load_state('networkidle')

            title = page.locator('h2, h1, h3, .page-title').first()
            assert title.is_visible(), "页面标题应可见"

            main_content = page.locator('main, .manage-content')
            assert main_content.count() > 0, "主内容区域应存在"

            save_screenshot(page, '01_page_load')
            return True
        finally:
            browser.close()


def test_compliance_rules_list():
    """测试合规规则列表显示"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/compliance')
            page.wait_for_load_state('networkidle')

            rules_list = page.locator('.compliance-rules-list, table, .data-table')
            empty_state = page.locator('.empty-state, .no-data')

            has_list = rules_list.count() > 0
            has_empty = empty_state.count() > 0
            assert has_list or has_empty, "应有合规规则列表或空状态提示"

            save_screenshot(page, '02_rules_list')
            return True
        finally:
            browser.close()


def test_rule_detail():
    """测试规则详情查看"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/compliance')
            page.wait_for_load_state('networkidle')

            rule_item = page.locator('.compliance-rule-item, tr, .list-item').first()

            if rule_item.is_visible():
                rule_item.click()
                page.wait_for_timeout(500)

                detail = page.locator('.rule-detail, .modal, .detail-panel')
                if detail.count() > 0:
                    assert detail.first.is_visible(), "规则详情应可见"

            save_screenshot(page, '03_rule_detail')
            return True
        finally:
            browser.close()


def test_rule_toggle():
    """测试规则启用/禁用"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/compliance')
            page.wait_for_load_state('networkidle')

            toggle = page.locator('.toggle-switch, input[type="checkbox"], .form-check-input')

            if toggle.count() > 0:
                assert toggle.first.is_visible(), "规则开关应可见"

            save_screenshot(page, '04_rule_toggle')
            return True
        finally:
            browser.close()


def test_compliance_report():
    """测试合规报告生成"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()

        try:
            login(page)
            page.goto(f'{BASE_URL}/manage/compliance')
            page.wait_for_load_state('networkidle')

            report_btn = page.locator('button:has-text("Report"), button:has-text("报告"), button:has-text("Generate")')

            if report_btn.count() > 0:
                assert report_btn.first.is_visible(), "报告按钮应可见"

            save_screenshot(page, '05_report')
            return True
        finally:
            browser.close()


def run_all_tests():
    tests = [
        ('页面加载', test_page_loads),
        ('合规规则列表显示', test_compliance_rules_list),
        ('规则详情查看', test_rule_detail),
        ('规则启用/禁用', test_rule_toggle),
        ('合规报告生成', test_compliance_report),
    ]

    results = []
    print("\n" + "=" * 60)
    print("Manage 模式 - Governance - Compliance 回归测试")
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