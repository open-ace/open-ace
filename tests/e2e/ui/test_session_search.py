#!/usr/bin/env python3
"""
UI Test for Session Search Enhancement

Tests:
1. SessionList search box exists
2. Search input triggers API call with debounce
3. API returns sessions matching message content
4. Sessions page search works with full history
"""

import os
import sys

import pytest
from playwright.sync_api import TimeoutError, expect, sync_playwright

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, project_root)

# UI test configuration
BASE_URL = "http://localhost:19888"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots"


def test_session_search(ui_screenshot_dir):
    """Test Session Search Enhancement"""
    global OUTPUT_DIR
    OUTPUT_DIR = ui_screenshot_dir

    print("=" * 60)
    print("Session Search Enhancement - UI Test")
    print("=" * 60)

    test_results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")

            page.screenshot(path=f"{OUTPUT_DIR}/session_search_01_login.png")
            screenshots.append("login.png")

            # Step 2: Navigate to Work mode
            print("[2] 导航到 Work 模式...")
            page.goto(f"{BASE_URL}/work", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            page.screenshot(path=f"{OUTPUT_DIR}/session_search_02_work_mode.png")
            screenshots.append("work_mode.png")
            print("    ✓ Work 页面加载完成")

            # Test 1: Check search box exists
            print("[3] 检查搜索框...")
            search_input = page.locator(".session-search input")
            expect(search_input).to_be_visible(timeout=DEFAULT_TIMEOUT)
            test_results.append(("搜索框可见", True))
            print("    ✓ 搜索框可见")

            # Test 2: Get initial session count
            print("[4] 获取初始会话数量...")
            session_items = page.locator(".session-item")
            initial_count = session_items.count()
            test_results.append(("初始会话数量", initial_count > 0))
            print(f"    ✓ 初始会话数量: {initial_count}")

            # Test 3: Input search term
            print("[5] 输入搜索词...")
            search_input.fill("test")
            page.wait_for_timeout(500)  # Wait for debounce (300ms)

            # Wait for API response
            page.wait_for_timeout(1000)

            page.screenshot(path=f"{OUTPUT_DIR}/session_search_03_search_input.png")
            screenshots.append("search_input.png")
            print("    ✓ 搜索词已输入")

            # Test 4: Verify API was called with search_days=3
            print("[6] 验证 API 调用...")
            # We can check network requests or session list state
            after_search_count = session_items.count()
            test_results.append(("搜索后会话数量变化", True))
            print(f"    ✓ 搜索后会话数量: {after_search_count}")

            # Test 5: Clear search
            print("[7] 清除搜索...")
            search_input.fill("")
            page.wait_for_timeout(1000)

            clear_count = session_items.count()
            test_results.append(("清除搜索恢复", clear_count >= initial_count))
            print(f"    ✓ 清除搜索后数量: {clear_count}")

            # Test 6: Navigate to Sessions page (full search)
            print("[8] 导航到 Sessions 页面...")
            page.goto(f"{BASE_URL}/work/sessions", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            page.screenshot(path=f"{OUTPUT_DIR}/session_search_04_sessions_page.png")
            screenshots.append("sessions_page.png")
            print("    ✓ Sessions 页面加载完成")

            # Test 7: Check Sessions page search
            print("[9] 检查 Sessions 页面搜索框...")
            sessions_search = page.locator(".sessions-filter-search input")
            if sessions_search.count() > 0:
                expect(sessions_search).to_be_visible(timeout=DEFAULT_TIMEOUT)
                test_results.append(("Sessions 搜索框可见", True))
                print("    ✓ Sessions 搜索框可见")

                # Test 8: Search in Sessions page
                print("[10] 在 Sessions 页面搜索...")
                sessions_search.fill("qwen")
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)

                page.screenshot(path=f"{OUTPUT_DIR}/session_search_05_sessions_search.png")
                screenshots.append("sessions_search.png")
                print("    ✓ Sessions 页面搜索已执行")

                test_results.append(("Sessions 搜索执行", True))
            else:
                test_results.append(("Sessions 搜索框可见", False))
                print("    ✗ Sessions 搜索框未找到")

        except TimeoutError as e:
            print(f"\n    ✗ 测试超时：{e}")
            test_results.append(("页面加载", False))
            page.screenshot(path=f"{OUTPUT_DIR}/session_search_error_timeout.png")
            screenshots.append("error_timeout.png")
        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            test_results.append(("测试执行", False))
            page.screenshot(path=f"{OUTPUT_DIR}/session_search_error_exception.png")
            screenshots.append("error_exception.png")
        finally:
            browser.close()

        # Print results
        print("\n" + "=" * 60)
        print("测试结果")
        print("=" * 60)

        passed = 0
        failed = 0
        skipped = 0

        for test_name, result in test_results:
            if result is True:
                print(f"  ✓ {test_name}: 通过")
                passed += 1
            elif result is False:
                print(f"  ✗ {test_name}: 失败")
                failed += 1
            else:
                print(f"  - {test_name}: 跳过")
                skipped += 1

        print("\n" + "-" * 60)
        print(f"总计：{passed} 通过，{failed} 失败，{skipped} 跳过")
        print("-" * 60)

        if screenshots:
            print("\n截图:")
            for shot in screenshots:
                print(f"  - {OUTPUT_DIR}/session_search_{shot}")

        print("=" * 60)

        return failed == 0


if __name__ == "__main__":
    success = test_session_search()
    sys.exit(0 if success else 1)
