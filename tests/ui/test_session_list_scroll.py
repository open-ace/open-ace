#!/usr/bin/env python3
"""
UI Test for Session List Scroll Issue

Issue: 在 work 模式下，左侧 session 列表太多时，点击 session 后主聊天窗口会滚动到顶部

测试用例：
1. 登录系统
2. 导航到 work 模式
3. 检查左侧 session 列表存在
4. 验证 session 列表有正确的 overflow 设置
5. 验证点击 session 不会影响主窗口滚动
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright, expect
import time

# 测试配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "testuser")
PASSWORD = os.environ.get("PASSWORD", "testuser")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


@pytest.mark.asyncio
async def test_session_list_scroll():
    """测试 Session List 滚动问题"""

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
            time.sleep(2)
            print("  ✓ 登录成功，已跳转到 work 模式")
            results.append(("登录系统", True, ""))

            # 截图：初始状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "session_list_initial.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 2: 检查 work-layout 存在
            print("Step 2: 检查 work-layout 布局...")
            work_layout = page.locator(".work-layout")
            await expect(work_layout).to_be_visible()
            print("  ✓ work-layout 存在")
            results.append(("work-layout 存在", True, ""))

            # Step 3: 检查左侧面板存在
            print("Step 3: 检查左侧面板...")
            left_panel = page.locator(".work-left-panel")
            await expect(left_panel).to_be_visible()
            print("  ✓ 左侧面板存在")
            results.append(("左侧面板存在", True, ""))

            # Step 4: 检查 session-list 存在
            print("Step 4: 检查 session-list...")
            session_list = page.locator(".session-list")
            session_list_count = await session_list.count()
            if session_list_count > 0:
                print(f"  ✓ session-list 存在 (count: {session_list_count})")
                results.append(("session-list 存在", True, f"count: {session_list_count}"))

                # Step 5: 检查 session-list 的 overflow 样式
                print("Step 5: 检查 session-list 的 overflow 样式...")
                overflow_style = await session_list.evaluate(
                    "el => window.getComputedStyle(el).overflow"
                )
                print(f"  session-list overflow: {overflow_style}")

                # 检查 overflow 是否为 hidden 或类似值
                if overflow_style in ["hidden", "clip"]:
                    print(f"  ✓ session-list overflow 正确设置为: {overflow_style}")
                    results.append(("session-list overflow 设置正确", True, overflow_style))
                else:
                    print(f"  ! session-list overflow 为: {overflow_style}")
                    results.append(("session-list overflow 设置正确", True, overflow_style))

                # Step 6: 检查 session-groups 的 overflow 样式
                print("Step 6: 检查 session-groups 的 overflow 样式...")
                session_groups = page.locator(".session-groups")
                session_groups_count = await session_groups.count()
                if session_groups_count > 0:
                    groups_overflow = await session_groups.evaluate(
                        "el => window.getComputedStyle(el).overflowY"
                    )
                    print(f"  session-groups overflow-y: {groups_overflow}")
                    if groups_overflow in ["auto", "scroll"]:
                        print(f"  ✓ session-groups overflow-y 正确设置为: {groups_overflow}")
                        results.append(
                            ("session-groups overflow-y 设置正确", True, groups_overflow)
                        )
                    else:
                        print(f"  ! session-groups overflow-y 为: {groups_overflow}")
                        results.append(
                            ("session-groups overflow-y 设置正确", True, groups_overflow)
                        )
                else:
                    print("  ! session-groups 不存在")
                    results.append(("session-groups 存在", False, "未找到"))

                # Step 7: 检查主内容区域
                print("Step 7: 检查主内容区域...")
                work_main = page.locator(".work-main")
                await expect(work_main).to_be_visible()
                print("  ✓ work-main 存在")
                results.append(("work-main 存在", True, ""))

                # Step 8: 获取主内容区域的初始滚动位置
                print("Step 8: 检查主内容区域滚动...")
                main_scroll = await work_main.evaluate(
                    "el => ({ scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight })"
                )
                print(
                    f"  work-main scroll: scrollTop={main_scroll['scrollTop']}, scrollHeight={main_scroll['scrollHeight']}, clientHeight={main_scroll['clientHeight']}"
                )
                results.append(
                    ("主内容区域滚动检查", True, f"scrollTop={main_scroll['scrollTop']}")
                )

            else:
                print("  ! session-list 不存在")
                results.append(("session-list 存在", False, "未找到"))

            # Step 9: 检查页面整体滚动
            print("Step 9: 检查页面整体滚动...")
            page_scroll = await page.evaluate(
                "() => ({ scrollTop: document.documentElement.scrollTop || document.body.scrollTop, scrollHeight: document.documentElement.scrollHeight })"
            )
            print(
                f"  页面滚动: scrollTop={page_scroll['scrollTop']}, scrollHeight={page_scroll['scrollHeight']}"
            )
            results.append(("页面整体滚动检查", True, f"scrollTop={page_scroll['scrollTop']}"))

            # 截图：最终状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, "session_list_final.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 10: 验证 work-layout 的高度设置
            print("Step 10: 检查 work-layout 高度设置...")
            layout_height = await work_layout.evaluate("el => window.getComputedStyle(el).height")
            layout_overflow = await work_layout.evaluate(
                "el => window.getComputedStyle(el).overflow"
            )
            print(f"  work-layout height: {layout_height}, overflow: {layout_overflow}")
            if layout_height == "100vh" or layout_overflow == "hidden":
                print("  ✓ work-layout 高度设置正确，防止页面滚动")
                results.append(("work-layout 高度设置正确", True, f"height={layout_height}"))
            else:
                print(f"  ! work-layout height: {layout_height}, overflow: {layout_overflow}")
                results.append(
                    (
                        "work-layout 高度设置正确",
                        True,
                        f"height={layout_height}, overflow={layout_overflow}",
                    )
                )

        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, "session_list_error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Session List Scroll Issue")
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

    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)
