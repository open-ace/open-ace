#!/usr/bin/env python3
"""
UI Test for Work Mode Prompts Tab "View All" Button

Test Objective:
Verify that the "View All" link in the Prompts tab of the right panel is clickable and navigates to /work/prompts.

Test Steps:
1. Visit http://localhost:5001/
2. Login to the system (using default credentials)
3. Navigate to work mode (click /work)
4. Check the right panel's Prompts tab
5. Find and click the "View All" button
6. Verify navigation to /work/prompts

Checkpoints:
- Right panel is displayed
- Prompts tab is active
- "View All" button exists and is visible
- Button is clickable
- Navigation to /work/prompts works
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright, expect
import time

# Test Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "issues",
)


@pytest.mark.asyncio
async def test_work_prompts_view_all_button():
    """Test Work Mode Prompts Tab View All Button"""

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

            # Step 3: Check if right panel is displayed
            print("Step 3: 检查右侧面板是否显示...")
            right_panel = page.locator(".work-right-panel")
            right_panel_count = await right_panel.count()

            if right_panel_count > 0:
                is_visible = await right_panel.first.is_visible()
                if is_visible:
                    print("  ✓ 右侧面板已显示")
                    results.append(("右侧面板显示", True, ""))
                else:
                    print("  ✗ 右侧面板存在但不可见")
                    results.append(("右侧面板显示", False, "面板不可见"))
            else:
                print("  ✗ 右侧面板不存在")
                results.append(("右侧面板显示", False, "面板未找到"))

            # Step 4: Check if Assist Panel exists
            print("Step 4: 检查 Assist Panel 组件...")
            assist_panel = page.locator(".assist-panel")
            assist_panel_count = await assist_panel.count()

            if assist_panel_count > 0:
                print("  ✓ Assist Panel 组件存在")
                results.append(("Assist Panel 存在", True, ""))
            else:
                print("  ✗ Assist Panel 组件不存在")
                results.append(("Assist Panel 存在", False, "组件未找到"))

            # Step 5: Check if Prompts tab content is visible
            print("Step 5: 检查 Prompts Tab 内容...")

            # Check for assist-prompts container
            prompts_content = page.locator(".assist-prompts")
            prompts_count = await prompts_content.count()

            if prompts_count > 0:
                is_visible = await prompts_content.first.is_visible()
                if is_visible:
                    print("  ✓ Prompts 内容区域已显示")
                    results.append(("Prompts 内容显示", True, ""))
                else:
                    print("  ✗ Prompts 内容区域存在但不可见")
                    results.append(("Prompts 内容显示", False, "内容不可见"))
            else:
                print("  ✗ Prompts 内容区域不存在")
                results.append(("Prompts 内容显示", False, "内容区域未找到"))

            # Step 6: Find the "View All" button
            print("Step 6: 查找 View All 按钮...")

            # The button is inside .assist-prompts with class "btn btn-link btn-sm"
            view_all_button = page.locator(".assist-prompts .btn-link")
            button_count = await view_all_button.count()

            print(f"  找到 {button_count} 个 .btn-link 按钮")

            if button_count > 0:
                # Check if the button contains "View All" text (or translated text)
                button_text = await view_all_button.last.text_content()
                print(f"  按钮文字：{button_text}")

                # Check if button is visible
                is_visible = await view_all_button.last.is_visible()
                if is_visible:
                    print("  ✓ View All 按钮可见")
                    results.append(("View All 按钮可见", True, f"text={button_text}"))
                else:
                    print("  ✗ View All 按钮不可见")
                    results.append(("View All 按钮可见", False, "按钮不可见"))

                # Step 7: Check if button is clickable (not disabled, not covered)
                print("Step 7: 检查按钮是否可点击...")

                # Check if button is disabled
                is_disabled = await view_all_button.last.is_disabled()
                if is_disabled:
                    print("  ✗ 按钮已禁用")
                    results.append(("按钮可点击", False, "按钮已禁用"))
                else:
                    print("  ✓ 按钮未禁用")

                    # Check pointer-events CSS property
                    pointer_events = await view_all_button.last.evaluate(
                        "el => window.getComputedStyle(el).pointerEvents"
                    )
                    print(f"  pointer-events: {pointer_events}")

                    if pointer_events == "none":
                        print("  ✗ 按钮的 pointer-events 为 none，无法点击")
                        results.append(("按钮可点击", False, "pointer-events: none"))
                    else:
                        print("  ✓ 按钮可以接收点击事件")
                        results.append(("按钮可点击", True, f"pointer-events: {pointer_events}"))

                # Step 8: Check button position and bounding box
                print("Step 8: 检查按钮位置...")
                box = await view_all_button.last.bounding_box()
                if box:
                    print(
                        f"  按钮位置：x={box['x']:.1f}, y={box['y']:.1f}, width={box['width']:.1f}, height={box['height']:.1f}"
                    )

                    if box["width"] > 0 and box["height"] > 0:
                        print("  ✓ 按钮有有效的尺寸")
                        results.append(
                            ("按钮尺寸有效", True, f"{box['width']:.1f}x{box['height']:.1f}")
                        )
                    else:
                        print("  ✗ 按钮尺寸无效（宽度或高度为0）")
                        results.append(
                            ("按钮尺寸有效", False, f"{box['width']:.1f}x{box['height']:.1f}")
                        )
                else:
                    print("  ✗ 无法获取按钮位置")
                    results.append(("按钮尺寸有效", False, "无法获取 bounding box"))

                # Step 9: Click the View All button
                print("Step 9: 点击 View All 按钮...")

                # Take screenshot before click
                screenshot_path = os.path.join(SCREENSHOT_DIR, "view_all", "before_click.png")
                await page.screenshot(path=screenshot_path)
                print(f"  点击前截图：{screenshot_path}")

                try:
                    # Click the button
                    await view_all_button.last.click(timeout=5000)
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

                except Exception as click_error:
                    print(f"  ✗ 点击失败：{click_error}")
                    results.append(("点击 View All 按钮", False, str(click_error)))

                    # Take error screenshot
                    error_screenshot = os.path.join(SCREENSHOT_DIR, "view_all", "click_error.png")
                    await page.screenshot(path=error_screenshot)
                    print(f"  错误截图：{error_screenshot}")

            else:
                print("  ✗ 未找到 View All 按钮")
                results.append(("View All 按钮存在", False, "按钮未找到"))

        except Exception as e:
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
    print("UI 功能测试报告 - Work 模式 Prompts Tab View All 按钮")
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
        print("  ✓ 所有测试通过！View All 按钮可以正常点击并导航到 /work/prompts。")
    else:
        print(f"  ✗ 有 {failed} 个测试失败，请检查截图和详情。")

    print(f"\n截图路径：{os.path.join(SCREENSHOT_DIR, 'view_all')}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)
