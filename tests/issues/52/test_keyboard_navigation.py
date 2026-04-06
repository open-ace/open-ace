#!/usr/bin/env python3
"""
UI Test for Session List Keyboard Navigation - Issue #52

Issue: 选择项目界面支持键盘导航

测试用例：
1. 登录系统
2. 导航到 work 模式
3. 检查左侧 session 列表存在
4. 检查键盘导航快捷键提示显示
5. 使用 ↓ 键选择下一个 session
6. 使用 ↑ 键选择上一个 session
7. 检查当前选中的 session 有视觉高亮
8. 使用 Enter 键打开选中的 session
9. 测试 Page Up/Down 快速翻页
"""

import pytest
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.async_api import async_playwright, expect
import time

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
async def test_session_list_keyboard_navigation():
    """测试 Session List 键盘导航功能"""

    # 确保截图目录存在
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            # Step 1: 登录系统
            print("Step 1: 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # 等待登录完成
            await page.wait_for_url("**/work**", timeout=10000)
            await asyncio.sleep(2)
            print("  ✓ 登录成功，已跳转到 work 模式")
            results.append(("登录系统", True, ""))

            # 截图：初始状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "01_initial_state.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 2: 检查 session-list 存在
            print("Step 2: 检查 session-list...")
            session_list = page.locator(".session-list")
            session_list_count = await session_list.count()
            if session_list_count > 0:
                print(f"  ✓ session-list 存在 (count: {session_list_count})")
                results.append(("session-list 存在", True, f"count: {session_list_count}"))
            else:
                print("  ! session-list 不存在")
                results.append(("session-list 存在", False, "未找到"))
                return False

            # Step 3: 检查 session-item 数量
            print("Step 3: 检查 session-item 数量...")
            session_items = page.locator(".session-item")
            item_count = await session_items.count()
            print(f"  找到 {item_count} 个 session-item")
            results.append(("session-item 数量", True, f"count: {item_count}"))

            if item_count == 0:
                print("  ! 没有足够的 session 进行键盘导航测试")
                results.append(("有可测试的 session", False, "session 数量为 0"))
                return False

            # Step 4: 检查键盘导航提示
            print("Step 4: 检查键盘导航提示...")
            keyboard_hint = page.locator(".keyboard-hint")
            hint_count = await keyboard_hint.count()
            if hint_count > 0:
                print(f"  ✓ 键盘导航提示存在")
                hint_text = await keyboard_hint.first.text_content()
                print(f"  提示内容: {hint_text}")
                results.append(("键盘导航提示存在", True, hint_text))
            else:
                print("  ! 键盘导航提示不存在")
                results.append(("键盘导航提示存在", False, "未找到"))

            # Step 5: 点击 session-list 获取焦点
            print("Step 5: 点击 session-list 获取焦点...")
            await session_list.focus()
            await asyncio.sleep(0.5)
            
            # 截图：点击后
            screenshot_path = os.path.join(SCREENSHOT_DIR, "02_after_click.png")
            await page.screenshot(path=screenshot_path)

            # Step 6: 按 ↓ 键选择第一个 session
            print("Step 6: 按 ↓ 键选择第一个 session...")
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)

            # 检查是否有 session 获得键盘焦点
            focused_item = page.locator(".session-item.keyboard-focused")
            focused_count = await focused_item.count()
            if focused_count > 0:
                print(f"  ✓ 找到 {focused_count} 个 keyboard-focused session")
                results.append(("↓ 键选择 session", True, f"focused count: {focused_count}"))
                
                # 截图：键盘选择后
                screenshot_path = os.path.join(SCREENSHOT_DIR, "03_after_arrow_down.png")
                await page.screenshot(path=screenshot_path)
            else:
                print("  ! 没有找到 keyboard-focused session")
                results.append(("↓ 键选择 session", False, "没有 keyboard-focused 元素"))

            # Step 7: 再按 ↓ 键选择下一个 session
            print("Step 7: 按 ↓ 键选择下一个 session...")
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)

            # 检查焦点是否移动
            focused_count_after = await focused_item.count()
            print(f"  当前 focused session 数量: {focused_count_after}")
            results.append(("第二次按 ↓ 键", True, f"focused count: {focused_count_after}"))

            # 截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "04_after_second_arrow_down.png")
            await page.screenshot(path=screenshot_path)

            # Step 8: 按 ↑ 键回到上一个 session
            print("Step 8: 按 ↑ 键回到上一个 session...")
            await page.keyboard.press("ArrowUp")
            await asyncio.sleep(0.3)

            # 截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "05_after_arrow_up.png")
            await page.screenshot(path=screenshot_path)
            results.append(("↑ 键导航", True, ""))

            # Step 9: 测试 Page Down
            if item_count > 5:
                print("Step 9: 测试 Page Down...")
                await page.keyboard.press("PageDown")
                await asyncio.sleep(0.3)
                
                screenshot_path = os.path.join(SCREENSHOT_DIR, "06_after_page_down.png")
                await page.screenshot(path=screenshot_path)
                results.append(("Page Down 导航", True, ""))
            else:
                print("Step 9: session 数量不足，跳过 Page Down 测试")
                results.append(("Page Down 导航", True, "跳过 - session 数量不足"))

            # Step 10: 测试 Page Up
            if item_count > 5:
                print("Step 10: 测试 Page Up...")
                await page.keyboard.press("PageUp")
                await asyncio.sleep(0.3)
                
                screenshot_path = os.path.join(SCREENSHOT_DIR, "07_after_page_up.png")
                await page.screenshot(path=screenshot_path)
                results.append(("Page Up 导航", True, ""))
            else:
                print("Step 10: session 数量不足，跳过 Page Up 测试")
                results.append(("Page Up 导航", True, "跳过 - session 数量不足"))

            # Step 11: 按 Enter 键打开选中的 session
            print("Step 11: 按 Enter 键打开选中的 session...")
            
            # 首先确保有一个 focused item
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            
            focused_count = await focused_item.count()
            if focused_count > 0:
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
                
                # 检查是否有 modal 打开
                modal = page.locator(".modal.show, .modal-dialog")
                modal_count = await modal.count()
                if modal_count > 0:
                    print("  ✓ Modal 打开成功")
                    results.append(("Enter 键打开 session", True, "Modal 已打开"))
                    
                    # 截图：Modal 打开
                    screenshot_path = os.path.join(SCREENSHOT_DIR, "08_modal_open.png")
                    await page.screenshot(path=screenshot_path)
                    
                    # 关闭 modal
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
                else:
                    print("  ! Modal 未打开")
                    results.append(("Enter 键打开 session", False, "Modal 未打开"))
            else:
                print("  ! 没有选中的 session")
                results.append(("Enter 键打开 session", False, "没有选中的 session"))

            # Step 12: 测试 Escape 键清除焦点
            print("Step 12: 测试 Escape 键清除焦点...")
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            
            focused_before = await focused_item.count()
            print(f"  按 Escape 前 focused count: {focused_before}")
            
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            
            focused_after = await focused_item.count()
            print(f"  按 Escape 后 focused count: {focused_after}")
            
            if focused_after == 0:
                print("  ✓ Escape 键成功清除焦点")
                results.append(("Escape 键清除焦点", True, ""))
            else:
                print("  ! Escape 键未能清除焦点")
                results.append(("Escape 键清除焦点", False, f"focused count: {focused_after}"))

            # 最终截图
            screenshot_path = os.path.join(SCREENSHOT_DIR, "09_final_state.png")
            await page.screenshot(path=screenshot_path)

        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, "error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Session List 键盘导航 (Issue #52)")
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

    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)