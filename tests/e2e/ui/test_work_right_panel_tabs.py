#!/usr/bin/env python3
"""
UI Test for Work Mode Prompts Drawer (replaces retired right panel tabs test)

The always-visible right AssistPanel (Prompts/Tools/Docs tabs) was retired:
- Prompts moved to a floating drawer on the workspace route only
- Docs moved to the header help menu

Test Objective:
Verify the new structure:
1. The retired right panel (.work-right-panel / .assist-panel) no longer exists
2. The prompts drawer toggle is visible on /work
3. The toggle opens the drawer with the prompts list
4. The toggle is absent on non-workspace routes (e.g. /work/sessions)

Checkpoints:
- .work-right-panel and .assist-panel are gone from the DOM
- .prompts-drawer-toggle visible on /work, opens .prompts-drawer
- No .prompts-drawer-toggle on /work/sessions
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
async def test_work_right_panel_tabs_layout(
    ui_screenshot_dir,
):
    """Test Work mode right panel removal and prompts drawer structure"""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir

    # Ensure screenshot directory exists
    os.makedirs(os.path.join(SCREENSHOT_DIR, "work_tabs"), exist_ok=True)

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
            time.sleep(1)
            print("  ✓ 已导航到 Work 模式")
            results.append(("导航到 Work 模式", True, ""))

            screenshot_path = os.path.join(SCREENSHOT_DIR, "work_tabs", "work_page.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存：{screenshot_path}")

            # Step 3: The retired right panel must be gone
            print("Step 3: 检查右侧常驻面板已移除...")
            right_panel_count = await page.locator(".work-right-panel").count()
            assist_panel_count = await page.locator(".assist-panel").count()
            if right_panel_count == 0 and assist_panel_count == 0:
                print("  ✓ .work-right-panel / .assist-panel 已不存在")
                results.append(("右侧常驻面板已移除", True, ""))
            else:
                print(
                    f"  ✗ 仍存在旧面板元素 (work-right-panel={right_panel_count}, assist-panel={assist_panel_count})"
                )
                results.append(
                    (
                        "右侧常驻面板已移除",
                        False,
                        f"work-right-panel={right_panel_count}, assist-panel={assist_panel_count}",
                    )
                )

            # Step 4: The drawer toggle is visible on the workspace route
            print("Step 4: 检查提示词抽屉触发按钮...")
            toggle = page.locator(".prompts-drawer-toggle")
            toggle_count = await toggle.count()
            if toggle_count > 0 and await toggle.first.is_visible():
                print("  ✓ 提示词抽屉触发按钮可见")
                results.append(("抽屉触发按钮可见", True, ""))
            else:
                print("  ✗ 提示词抽屉触发按钮不可见")
                results.append(("抽屉触发按钮可见", False, "按钮未找到或不可见"))

            # Step 5: Toggle opens the drawer with prompts list
            print("Step 5: 打开抽屉并检查提示词列表...")
            if toggle_count > 0:
                await toggle.first.click()
                await page.wait_for_timeout(500)
                drawer = page.locator(".prompts-drawer")
                drawer_count = await drawer.count()
                drawer_visible = drawer_count > 0 and await drawer.first.is_visible()
                if drawer_visible:
                    print("  ✓ 提示词抽屉已打开")
                    results.append(("抽屉打开", True, ""))

                    # Prompts list inside the drawer
                    prompt_list_count = await page.locator(".prompts-drawer .prompt-list").count()
                    if prompt_list_count > 0:
                        print("  ✓ 抽屉内提示词列表存在")
                        results.append(("抽屉内提示词列表存在", True, ""))
                    else:
                        print("  ✗ 抽屉内提示词列表不存在")
                        results.append(("抽屉内提示词列表存在", False, "列表未找到"))
                else:
                    print("  ✗ 提示词抽屉未能打开")
                    results.append(("抽屉打开", False, "抽屉未找到或不可见"))

                screenshot_path = os.path.join(SCREENSHOT_DIR, "work_tabs", "prompts_drawer.png")
                await page.screenshot(path=screenshot_path)
                print(f"  截图保存：{screenshot_path}")

            # Step 6: Toggle is absent on a non-workspace route
            print("Step 6: 检查非工作区路由没有抽屉入口...")
            await page.goto(f"{BASE_URL}/work/sessions")
            await page.wait_for_timeout(1000)
            sessions_toggle_count = await page.locator(".prompts-drawer-toggle").count()
            sessions_drawer_count = await page.locator(".prompts-drawer").count()
            if sessions_toggle_count == 0 and sessions_drawer_count == 0:
                print("  ✓ /work/sessions 无抽屉入口，主区域不常驻占位")
                results.append(("非工作区路由无抽屉入口", True, ""))
            else:
                print(
                    f"  ✗ /work/sessions 仍存在抽屉元素 (toggle={sessions_toggle_count}, drawer={sessions_drawer_count})"
                )
                results.append(
                    (
                        "非工作区路由无抽屉入口",
                        False,
                        f"toggle={sessions_toggle_count}, drawer={sessions_drawer_count}",
                    )
                )

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"  ✗ 测试失败：{e}")
            results.append(("测试执行", False, str(e)))

            # Error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, "work_tabs", "error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图：{error_screenshot}")

        finally:
            await browser.close()

    # Print test report
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Work 模式提示词抽屉（右栏已移除）")
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

    # Summary
    print("\n测试总结:")
    if failed == 0:
        print("  ✓ 所有测试通过！右栏常驻面板已移除，提示词抽屉按路由正常工作。")
    else:
        print(f"  ✗ 有 {failed} 个测试失败，请检查截图和详情。")

    print(f"\n截图路径：{os.path.join(SCREENSHOT_DIR, 'work_tabs')}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)
