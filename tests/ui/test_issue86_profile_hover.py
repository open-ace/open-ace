#!/usr/bin/env python3
"""
UI Test for Issue 86

Issue 86: 菜单栏用户名称移到 Logout 按钮上悬停显示

测试用例：
1. 登录系统
2. 验证 Profile 链接不存在
3. 验证 Logout 按钮存在
4. 验证默认状态显示 "Logout"
5. 验证悬停时显示 "Logout 用户名"
6. 验证移开鼠标后恢复 "Logout"
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright, expect
import time

# 测试配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'testuser')
PASSWORD = os.environ.get('PASSWORD', 'testuser')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'issues')

@pytest.mark.asyncio
async def test_issue86():
    """测试 Issue 86"""

    # 确保截图目录存在
    os.makedirs(os.path.join(SCREENSHOT_DIR, '86'), exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900}
        )
        page = await context.new_page()

        try:
            # Step 1: 登录系统
            print("Step 1: 登录系统...")
            await page.goto(f'{BASE_URL}/login')
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')

            # 等待登录完成
            await page.wait_for_url('**/', timeout=10000)
            time.sleep(1)
            print("  ✓ 登录成功")
            results.append(("登录系统", True, ""))

            # Step 2: 验证 Profile 链接不存在
            print("Step 2: 验证 Profile 链接不存在...")

            nav_profile = page.locator('#nav-profile')
            profile_count = await nav_profile.count()

            if profile_count == 0:
                print("  ✓ Profile 链接已移除")
                results.append(("Profile 链接已移除", True, ""))
            else:
                print(f"  ✗ Profile 链接仍然存在 (count: {profile_count})")
                results.append(("Profile 链接已移除", False, f"找到 {profile_count} 个"))

            # 截图：当前状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'sidebar_no_profile.png')
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 3: 验证 Logout 按钮存在
            print("Step 3: 验证 Logout 按钮存在...")

            nav_logout = page.locator('#nav-logout')
            await expect(nav_logout).to_be_visible()
            print("  ✓ Logout 按钮可见")
            results.append(("Logout 按钮可见", True, ""))

            # Step 4: 验证默认状态显示 "Logout"
            print("Step 4: 验证默认状态显示 'Logout'...")

            logout_text_el = page.locator('#nav-logout-text')
            logout_text = await logout_text_el.text_content()

            if logout_text == 'Logout':
                print(f"  ✓ 默认状态正确: {logout_text}")
                results.append(("默认状态显示 Logout", True, f"显示: {logout_text}"))
            else:
                print(f"  ✗ 默认状态不正确: 期望 'Logout', 实际 '{logout_text}'")
                results.append(("默认状态显示 Logout", False, f"期望 'Logout', 实际 '{logout_text}'"))

            # 截图：默认状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'logout_default.png')
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 5: 验证悬停时显示 "Logout 用户名"
            print("Step 5: 验证悬停时显示 'Logout 用户名'...")

            # 悬停在 Logout 按钮上
            await nav_logout.hover()
            time.sleep(0.3)

            # 获取悬停后的文字
            hover_text = await logout_text_el.text_content()
            expected_hover_text = f'Logout {USERNAME}'

            if hover_text == expected_hover_text:
                print(f"  ✓ 悬停状态正确: {hover_text}")
                results.append(("悬停显示用户名", True, f"显示: {hover_text}"))
            else:
                print(f"  ✗ 悬停状态不正确: 期望 '{expected_hover_text}', 实际 '{hover_text}'")
                results.append(("悬停显示用户名", False, f"期望 '{expected_hover_text}', 实际 '{hover_text}'"))

            # 截图：悬停状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'logout_hover.png')
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 6: 验证移开鼠标后恢复 "Logout"
            print("Step 6: 验证移开鼠标后恢复 'Logout'...")

            # 移动鼠标到其他位置
            await page.mouse.move(0, 0)
            time.sleep(0.3)

            # 获取移开后的文字
            final_text = await logout_text_el.text_content()

            if final_text == 'Logout':
                print(f"  ✓ 移开鼠标后恢复: {final_text}")
                results.append(("移开鼠标恢复 Logout", True, f"显示: {final_text}"))
            else:
                print(f"  ✗ 移开鼠标后未恢复: 期望 'Logout', 实际 '{final_text}'")
                results.append(("移开鼠标恢复 Logout", False, f"期望 'Logout', 实际 '{final_text}'"))

            # 截图：最终状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'logout_final.png')
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, 'error.png')
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Issue 86")
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

if __name__ == '__main__':
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)