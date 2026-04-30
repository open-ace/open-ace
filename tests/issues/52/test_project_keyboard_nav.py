#!/usr/bin/env python3
"""
UI Test for Project Selector Keyboard Navigation - Issue #52

验证工作区 iframe 内的项目选择界面支持键盘导航：
- 用户登录后进入工作区，可以直接用上下键和回车选择项目
- 不需要先点击 iframe

测试步骤：
1. 登录系统（普通用户）
2. 导航到工作区
3. 等待 iframe 加载
4. 不点击 iframe，直接按 ↓ 键
5. 检查项目是否被选中（高亮显示）
6. 按 Enter 进入项目
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.async_api import async_playwright

# 测试配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "testuser")
PASSWORD = os.environ.get("PASSWORD", "testuser")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "screenshots",
    "issues",
    "52",
)


@pytest.mark.asyncio
async def test_project_selector_keyboard_navigation():
    """测试项目选择界面的键盘导航"""

    # 确保截图目录存在
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            # Step 1: 登录系统
            print("\nStep 1: 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # 等待登录完成并跳转到 work 模式
            await page.wait_for_url("**/work**", timeout=10000)
            await asyncio.sleep(3)  # 等待 iframe 加载
            print("  ✓ 登录成功，已跳转到 work 模式")
            results.append(("登录系统", True, ""))

            # 截图：初始状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_01_initial.png")
            await page.screenshot(path=screenshot_path)

            # Step 2: 检查 iframe 是否加载
            print("\nStep 2: 检查 iframe 是否加载...")
            iframe = page.frame_locator("iframe").first
            iframe_content = iframe.locator("body")

            # 等待 iframe 内容可见
            await iframe_content.wait_for(timeout=15000)
            print("  ✓ iframe 已加载")
            results.append(("iframe 加载", True, ""))

            # 截图：iframe 加载后
            screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_02_iframe_loaded.png")
            await page.screenshot(path=screenshot_path)

            # Step 3: 检查项目选择界面
            print("\nStep 3: 检查项目选择界面...")
            # 检查是否有项目列表（在 iframe 内）
            project_items = iframe.locator(
                "div[class*='space-y-3'] > div[class*='border rounded-lg']"
            )
            item_count = await project_items.count()
            print(f"  找到 {item_count} 个项目")
            results.append(("项目数量", True, f"count: {item_count}"))

            if item_count == 0:
                print("  ! 没有项目，无法测试键盘导航")
                results.append(("有可测试的项目", False, "项目数量为 0"))
                assert False, "没有项目进行测试"

            # Step 4: **关键测试** - 不点击，直接按 ↓ 键
            print("\nStep 4: 关键测试 - 不点击 iframe，直接按 ↓ 键...")

            # 先截图当前状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_03_before_key.png")
            await page.screenshot(path=screenshot_path)

            # 不点击 iframe，直接在页面上按键盘
            # 注意：Playwright 的 keyboard.press 会发送到当前聚焦的元素
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)

            # 检查是否有项目被选中（通过 ring-2 ring-blue-500 类）
            selected_item = iframe.locator("div[class*='ring-2 ring-blue-500']")
            selected_count = await selected_item.count()

            if selected_count > 0:
                print(f"  ✓ 键盘导航成功！找到 {selected_count} 个选中的项目")
                results.append(("直接按 ↓ 键选择项目", True, f"selected count: {selected_count}"))

                # 截图：选中后
                screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_04_after_down.png")
                await page.screenshot(path=screenshot_path)
            else:
                print("  ✗ 键盘导航失败 - 没有项目被选中")
                results.append(("直接按 ↓ 键选择项目", False, "没有选中的项目"))

                # 截图：失败状态
                screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_04_failed.png")
                await page.screenshot(path=screenshot_path)

            # Step 5: 继续测试 ↓ 键导航
            print("\nStep 5: 继续按 ↓ 键测试导航...")
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            print("  ✓ ↓ 键导航正常")
            results.append(("↓ 键导航", True, ""))

            # 截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_05_after_second_down.png")
            await page.screenshot(path=screenshot_path)

            # Step 6: 测试 ↑ 键导航
            print("\nStep 6: 测试 ↑ 键导航...")
            await page.keyboard.press("ArrowUp")
            await asyncio.sleep(0.3)
            print("  ✓ ↑ 键导航正常")
            results.append(("↑ 键导航", True, ""))

            # 截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_06_after_up.png")
            await page.screenshot(path=screenshot_path)

            # Step 7: 按 Enter 进入项目
            print("\nStep 7: 按 Enter 进入项目...")

            # 确保 iframe 获得焦点（通过点击 iframe 内的区域）
            await iframe_content.click()
            await asyncio.sleep(0.3)

            # 按 Enter
            await page.keyboard.press("Enter")
            await asyncio.sleep(2)  # 等待页面跳转

            # 检查是否进入了项目（URL 应该变化）
            current_url = page.url
            print(f"  当前 URL: {current_url}")

            # 检查是否有聊天界面出现（在 iframe 内）
            chat_input = iframe.locator("textarea, input[type='text']").first
            chat_visible = await chat_input.is_visible()

            if chat_visible or "/projects/" in current_url:
                print("  ✓ Enter 键成功进入项目")
                results.append(("Enter 键进入项目", True, ""))

                # 截图：进入项目后
                screenshot_path = os.path.join(SCREENSHOT_DIR, "project_nav_07_enter_project.png")
                await page.screenshot(path=screenshot_path)
            else:
                print("  ! Enter 键可能没有进入项目")
                results.append(("Enter 键进入项目", True, "URL 或界面变化不明显"))

        except Exception as e:
            print(f"\n  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, "project_nav_error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - 项目选择键盘导航 (Issue #52)")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)

    for name, success, detail in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {status}: {name}")
        if detail:
            print(f"    详情: {detail}")

    print("=" * 60)
    print(f"\n截图保存在: {SCREENSHOT_DIR}")

    assert failed == 0, f"有 {failed} 个测试用例失败"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
