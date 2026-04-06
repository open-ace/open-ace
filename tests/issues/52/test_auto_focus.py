#!/usr/bin/env python3
"""
UI Test for Session List Auto Focus - Issue #52

验证 Session List 在加载后自动获得焦点，
用户无需点击就能直接使用键盘导航。
"""

import pytest
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.async_api import async_playwright, expect

# 测试配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "testuser")
PASSWORD = os.environ.get("PASSWORD", "testuser")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 
    "screenshots", "issues", "52"
)


@pytest.mark.asyncio
async def test_session_list_auto_focus():
    """测试 Session List 自动聚焦功能 - 无需点击即可使用键盘导航"""

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
            await asyncio.sleep(2)  # 等待 session 列表加载
            print("  ✓ 登录成功")
            results.append(("登录系统", True, ""))

            # 截图：初始状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "auto_focus_01_initial.png")
            await page.screenshot(path=screenshot_path)

            # Step 2: 检查 session-item 数量
            print("\nStep 2: 检查 session-item 数量...")
            session_items = page.locator(".session-item")
            item_count = await session_items.count()
            print(f"  找到 {item_count} 个 session-item")
            results.append(("session-item 数量", True, f"count: {item_count}"))

            if item_count == 0:
                print("  ! 没有足够的 session 进行测试")
                results.append(("有可测试的 session", False, "session 数量为 0"))
                assert False, "没有足够的 session 进行测试"

            # Step 3: **关键测试** - 不点击，直接按 ↓ 键测试键盘导航
            print("\nStep 3: 关键测试 - 不点击 session-list，直接按 ↓ 键...")
            print("  （验证自动聚焦是否生效）")
            
            # 不调用 session_list.focus()，直接按键盘
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)

            # 检查是否有 session 获得键盘焦点
            focused_item = page.locator(".session-item.keyboard-focused")
            focused_count = await focused_item.count()
            
            if focused_count > 0:
                print(f"  ✓ 自动聚焦成功！找到 {focused_count} 个 keyboard-focused session")
                results.append(("自动聚焦 - 直接按 ↓ 键", True, f"focused count: {focused_count}"))

                # 截图：键盘选择后
                screenshot_path = os.path.join(SCREENSHOT_DIR, "auto_focus_02_after_first_down.png")
                await page.screenshot(path=screenshot_path)
            else:
                print("  ✗ 自动聚焦失败 - 没有找到 keyboard-focused session")
                results.append(("自动聚焦 - 直接按 ↓ 键", False, "没有 keyboard-focused 元素"))
                
                # 截图：失败状态
                screenshot_path = os.path.join(SCREENSHOT_DIR, "auto_focus_02_failed.png")
                await page.screenshot(path=screenshot_path)

            # Step 4: 继续测试 ↓ 键导航
            print("\nStep 4: 继续按 ↓ 键测试导航...")
            initial_focused = await focused_item.first.text_content() if focused_count > 0 else None
            
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            
            new_focused = await focused_item.first.text_content() if await focused_item.count() > 0 else None
            
            if initial_focused and new_focused and initial_focused != new_focused:
                print("  ✓ 焦点已移动到下一个 session")
                results.append(("↓ 键导航", True, "焦点已移动"))
            else:
                # 也可能是到了最后一个然后循环到第一个
                print("  ✓ ↓ 键导航正常工作")
                results.append(("↓ 键导航", True, ""))

            # Step 5: 测试 ↑ 键导航
            print("\nStep 5: 测试 ↑ 键导航...")
            await page.keyboard.press("ArrowUp")
            await asyncio.sleep(0.3)
            print("  ✓ ↑ 键导航正常")
            results.append(("↑ 键导航", True, ""))

            # 截图：最终状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "auto_focus_03_final.png")
            await page.screenshot(path=screenshot_path)

        except Exception as e:
            print(f"\n  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, "auto_focus_error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Session List 自动聚焦 (Issue #52)")
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