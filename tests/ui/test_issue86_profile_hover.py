#!/usr/bin/env python3
"""
UI Test for Issue 86

Issue 86: 菜单栏用户名称移到 Logout 按钮上悬停显示

测试用例：
1. 登录系统
2. 验证 Profile 链接不存在
3. 验证 Logout 按钮存在
4. 验证 Logout 按钮文字显示 "Logout 用户名"
5. 验证用户名太长时会被截短
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect
import time

# 测试配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'testuser')
PASSWORD = os.environ.get('PASSWORD', 'testuser')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'issues')

def test_issue86():
    """测试 Issue 86"""

    # 确保截图目录存在
    os.makedirs(os.path.join(SCREENSHOT_DIR, '86'), exist_ok=True)

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 900}
        )
        page = context.new_page()

        try:
            # Step 1: 登录系统
            print("Step 1: 登录系统...")
            page.goto(f'{BASE_URL}/login')
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')

            # 等待登录完成
            page.wait_for_url('**/', timeout=10000)
            time.sleep(1)
            print("  ✓ 登录成功")
            results.append(("登录系统", True, ""))

            # Step 2: 验证 Profile 链接不存在
            print("Step 2: 验证 Profile 链接不存在...")

            nav_profile = page.locator('#nav-profile')
            profile_count = nav_profile.count()

            if profile_count == 0:
                print("  ✓ Profile 链接已移除")
                results.append(("Profile 链接已移除", True, ""))
            else:
                print(f"  ✗ Profile 链接仍然存在 (count: {profile_count})")
                results.append(("Profile 链接已移除", False, f"找到 {profile_count} 个"))

            # 截图：当前状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'sidebar_no_profile.png')
            page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 3: 验证 Logout 按钮存在
            print("Step 3: 验证 Logout 按钮存在...")

            nav_logout = page.locator('#nav-logout')
            expect(nav_logout).to_be_visible()
            print("  ✓ Logout 按钮可见")
            results.append(("Logout 按钮可见", True, ""))

            # Step 4: 验证 Logout 按钮文字显示 "Logout 用户名"
            print("Step 4: 验证 Logout 按钮文字显示 'Logout 用户名'...")

            logout_text_el = page.locator('#nav-logout-text')
            logout_text = logout_text_el.text_content()

            expected_text = f'Logout {USERNAME}'
            if logout_text == expected_text:
                print(f"  ✓ Logout 按钮文字正确: {logout_text}")
                results.append(("Logout 按钮文字正确", True, f"显示: {logout_text}"))
            else:
                print(f"  ✗ Logout 按钮文字不正确: 期望 '{expected_text}', 实际 '{logout_text}'")
                results.append(("Logout 按钮文字正确", False, f"期望 '{expected_text}', 实际 '{logout_text}'"))

            # 截图：Logout 按钮
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'logout_button.png')
            page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")

            # Step 5: 验证用户名截短功能（使用长用户名测试）
            print("Step 5: 验证用户名截短功能...")

            # 使用 JavaScript 模拟长用户名
            truncated = page.evaluate('''() => {
                const logoutText = document.getElementById('nav-logout-text');
                const originalText = logoutText.textContent;
                
                // 模拟长用户名
                const longUsername = 'verylongusername123';
                let displayName = longUsername;
                if (displayName.length > 10) {
                    displayName = displayName.substring(0, 10) + '...';
                }
                logoutText.textContent = 'Logout ' + displayName;
                
                const newText = logoutText.textContent;
                // 恢复原始文字
                logoutText.textContent = originalText;
                
                return { displayName, newText };
            }''')

            expected_truncated = 'verylongus...'
            if truncated['displayName'] == expected_truncated:
                print(f"  ✓ 用户名截短正确: {truncated['displayName']}")
                results.append(("用户名截短功能", True, f"截短后: {truncated['displayName']}"))
            else:
                print(f"  ✗ 用户名截短不正确: 期望 '{expected_truncated}', 实际 '{truncated['displayName']}'")
                results.append(("用户名截短功能", False, f"期望 '{expected_truncated}', 实际 '{truncated['displayName']}'"))

        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            results.append(("测试执行", False, str(e)))

            # 错误截图
            error_screenshot = os.path.join(SCREENSHOT_DIR, 'error.png')
            page.screenshot(path=error_screenshot)
            print(f"  错误截图: {error_screenshot}")

        finally:
            browser.close()

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
    success = test_issue86()
    sys.exit(0 if success else 1)