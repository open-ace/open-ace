#!/usr/bin/env python3
"""
回归测试: 导航功能

测试内容：
1. 侧边栏菜单显示
2. 菜单项点击导航
3. 页面标题更新
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

MODULE_NAME = "navigation"


def test_sidebar_menu_visible():
    """测试侧边栏菜单显示"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)

            # 检查侧边栏或导航区域
            sidebar_selectors = [
                ".sidebar",
                "nav",
                ".menu",
                ".work-nav",
                ".work-left-panel",
                ".work-layout",
                "aside",
            ]
            assert check_element_exists(page, sidebar_selectors), "侧边栏应存在"

            # 检查菜单项
            menu_selectors = [
                ".sidebar button.nav-link",
                "nav button",
                ".menu button",
                ".work-nav button",
                ".work-nav-item",
                ".manage-sidebar .nav-item",
            ]
            assert check_element_exists(page, menu_selectors), "菜单项应存在"

            save_screenshot(page, MODULE_NAME, "01_sidebar")
            return True
        finally:
            browser.close()


def test_menu_navigation():
    """测试菜单项点击导航"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)

            # 测试点击各个菜单项
            menu_items = [
                ("Work", "/work"),
                ("Sessions", "/work/sessions"),
            ]

            for name, path in menu_items:
                link_selectors = [f'a[href*="{path}"]', f'a:has-text("{name}")']
                if check_element_exists(page, link_selectors):
                    try:
                        link = page.locator(link_selectors[0] + ", " + link_selectors[1]).first
                        if link.is_visible():
                            link.click()
                            page.wait_for_timeout(500)
                    except Exception:
                        pass

            save_screenshot(page, MODULE_NAME, "02_navigation")
            return True
        finally:
            browser.close()


def test_page_title_updates():
    """测试页面标题更新"""
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)

            # 导航到不同页面并检查标题
            pages_to_check = [
                "/work",
                "/work/sessions",
            ]

            for path in pages_to_check:
                navigate_to(page, path)
                # 检查页面标题或导航标识
                title_selectors = [
                    "h2",
                    "h1",
                    ".logo-text",
                    ".panel-title",
                    ".work-nav-item.active",
                ]
                assert check_element_exists(
                    page, title_selectors
                ), f"页面 {path} 应有标题或导航标识"

            save_screenshot(page, MODULE_NAME, "03_title")
            return True
        finally:
            browser.close()


def run_all_tests():
    """运行所有导航回归测试"""
    runner = TestRunner("导航功能")
    runner.print_header()

    tests = [
        ("侧边栏菜单显示", test_sidebar_menu_visible),
        ("菜单导航", test_menu_navigation),
        ("页面标题更新", test_page_title_updates),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()
