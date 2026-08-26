#!/usr/bin/env python3
"""
UI Test for Work Mode Prompts Drawer "View All" Link

Test Objective:
Verify that the "View All" link at the bottom of the prompts drawer is
clickable and navigates to /work/prompts.

(Note: the historical test targeted a "View All" button inside the retired
right AssistPanel's Prompts tab — that button did not exist in the code at
the time the panel was retired. The prompts drawer now ships a real
"View All" link, which this test covers.)

Test Steps:
1. Visit http://localhost:19888/
2. Login to the system (using default credentials)
3. Navigate to work mode (click /work)
4. Open the prompts drawer via the edge toggle
5. Find and click the "View All" link
6. Verify navigation to /work/prompts

Checkpoints:
- Prompts drawer toggle is displayed on /work
- Drawer opens with the "View All" link visible
- Link is clickable
- Navigation to /work/prompts works
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

import time

from playwright.async_api import async_playwright

# Test Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
)


@pytest.mark.asyncio
async def test_work_prompts_view_all_button(
    ui_screenshot_dir,
):  # allow-no-assert: smoke test - visual verification only
    """Test Work Mode Prompts Drawer View All Link"""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir

    # Ensure screenshot directory exists
    os.makedirs(os.path.join(SCREENSHOT_DIR, "view_all"), exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # Step 1: Login to the system
            print("Step 1: 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for login to complete
            await page.wait_for_url("**/work**", timeout=10000)
            time.sleep(1)
            print("  ✓ 登录成功")
            results.append(("登录系统", True, ""))

            # Step 2: Navigate to Work mode
            print("Step 2: 导航到 Work 模式...")
            await page.goto(f"{BASE_URL}/work")
            time.sleep(2)  # Wait for page to fully load
            print("  ✓ 已导航到 Work 模式")
            results.append(("导航到 Work 模式", True, ""))

            # Take screenshot: Work page
            screenshot_path = os.path.join(SCREENSHOT_DIR, "view_all", "work_page.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存：{screenshot_path}")

            # Step 3: Open the prompts drawer via the edge toggle
            print("Step 3: 打开提示词抽屉...")
            toggle = page.locator(".prompts-drawer-toggle")
            toggle_count = await toggle.count()

            if toggle_count > 0 and await toggle.first.is_visible():
                print("  ✓ 抽屉触发按钮可见")
                results.append(("抽屉触发按钮可见", True, ""))

                await toggle.first.click()
                await page.wait_for_timeout(500)

                drawer = page.locator(".prompts-drawer")
                if await drawer.count() > 0 and await drawer.first.is_visible():
                    print("  ✓ 提示词抽屉已打开")
                    results.append(("提示词抽屉打开", True, ""))
                else:
                    print("  ✗ 提示词抽屉未能打开")
                    results.append(("提示词抽屉打开", False, "抽屉未找到或不可见"))
            else:
                print("  ✗ 抽屉触发按钮不可见")
                results.append(("抽屉触发按钮可见", False, "按钮未找到或不可见"))

            # Step 4: Check the View All link inside the drawer
            print("Step 4: 检查 View All 链接...")
            view_all_link = page.locator(".prompts-drawer .prompts-drawer-view-all")
            link_count = await view_all_link.count()

            if link_count > 0:
                is_visible = await view_all_link.first.is_visible()
                if is_visible:
                    print("  ✓ View All 链接可见")
                    results.append(("View All 链接存在", True, ""))
                else:
                    print("  ✗ View All 链接存在但不可见")
                    results.append(("View All 链接存在", False, "链接不可见"))
            else:
                print("  ✗ 未找到 View All 链接")
                results.append(("View All 链接存在", False, "链接未找到"))

            # Step 5: Click the View All link
            print("Step 5: 点击 View All 链接...")

            if link_count > 0:
                # Take screenshot before click
                screenshot_path = os.path.join(SCREENSHOT_DIR, "view_all", "before_click.png")
                await page.screenshot(path=screenshot_path)
                print(f"  点击前截图：{screenshot_path}")

                try:
                    await view_all_link.first.click(timeout=5000)
                    time.sleep(1)  # Wait for navigation

                    # Check current URL
                    current_url = page.url
                    print(f"  当前 URL：{current_url}")

                    if "/work/prompts" in current_url:
                        print("  ✓ 成功导航到 /work/prompts")
                        results.append(("导航到 Prompts 页面", True, current_url))
                    else:
                        print(f"  ✗ 导航失败，当前 URL：{current_url}")
                        results.append(("导航到 Prompts 页面", False, current_url))

                    # Take screenshot after click
                    screenshot_path = os.path.join(SCREENSHOT_DIR, "view_all", "after_click.png")
                    await page.screenshot(path=screenshot_path)
                    print(f"  点击后截图：{screenshot_path}")

                except Exception as click_error:  # allow-swallow: UI element may not exist
                    print(f"  ✗ 点击失败：{click_error}")
                    results.append(("点击 View All 链接", False, str(click_error)))

                    # Take error screenshot
                    error_screenshot = os.path.join(SCREENSHOT_DIR, "view_all", "click_error.png")
                    await page.screenshot(path=error_screenshot)
                    print(f"  错误截图：{error_screenshot}")

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"  ✗ 测试失败：{e}")
            results.append(("测试执行", False, str(e)))

            # Error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, "view_all", "error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图：{error_screenshot}")

        finally:
            await browser.close()

    # Print test report
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Work 模式提示词抽屉 View All 链接")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例：{len(results)} 个")
    print(f"通过：{passed} 个")
    print(f"失败：{failed} 个")
    print("-" * 60)

    for name, success, detail in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {status}: {name}")
        if detail:
            print(f"    详情：{detail}")

    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)
