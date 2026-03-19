#!/usr/bin/env python3
"""
UI Test for Issue 86

Issue 86: 菜单栏用户名称移到 Logout 按钮上悬停显示

测试用例：
1. 登录系统
2. 验证默认状态下只显示用户图标，不显示用户名
3. 悬停在用户图标上，验证用户名显示
4. 移开鼠标，验证用户名隐藏
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
            
            # Step 2: 验证默认状态下只显示用户图标，不显示用户名
            print("Step 2: 验证默认状态下只显示用户图标...")
            
            # 检查 nav-profile 存在
            nav_profile = page.locator('#nav-profile')
            expect(nav_profile).to_be_visible()
            print("  ✓ nav-profile 可见")
            
            # 检查 nav-profile-text 默认隐藏
            profile_text = page.locator('#nav-profile-text')
            
            # 使用 JavaScript 检查 CSS display 属性
            is_hidden = page.evaluate('''() => {
                const el = document.getElementById('nav-profile-text');
                const style = window.getComputedStyle(el);
                return style.display === 'none';
            }''')
            
            if is_hidden:
                print("  ✓ 默认状态下用户名隐藏")
                results.append(("默认状态: 用户名隐藏", True, ""))
            else:
                print("  ✗ 默认状态下用户名未隐藏")
                results.append(("默认状态: 用户名隐藏", False, "用户名仍然可见"))
            
            # 截图：默认状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'profile_default.png')
            page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")
            
            # Step 3: 悬停在用户图标上，验证用户名显示
            print("Step 3: 悬停在用户图标上，验证用户名显示...")
            
            # 悬停在 nav-profile 上
            nav_profile.hover()
            time.sleep(0.5)
            
            # 检查 nav-profile-text 显示
            is_visible = page.evaluate('''() => {
                const el = document.getElementById('nav-profile-text');
                const style = window.getComputedStyle(el);
                return style.display !== 'none';
            }''')
            
            if is_visible:
                print("  ✓ 悬停状态下用户名显示")
                results.append(("悬停状态: 用户名显示", True, ""))
            else:
                print("  ✗ 悬停状态下用户名未显示")
                results.append(("悬停状态: 用户名显示", False, "用户名仍然隐藏"))
            
            # 截图：悬停状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'profile_hover.png')
            page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")
            
            # Step 4: 验证用户名内容正确
            print("Step 4: 验证用户名内容正确...")
            
            # 获取显示的用户名
            displayed_username = profile_text.text_content()
            if displayed_username and displayed_username.strip():
                print(f"  ✓ 用户名显示为: {displayed_username}")
                results.append(("用户名内容正确", True, f"显示: {displayed_username}"))
            else:
                print("  ✗ 用户名内容为空")
                results.append(("用户名内容正确", False, "用户名为空"))
            
            # Step 5: 移开鼠标，验证用户名隐藏
            print("Step 5: 移开鼠标，验证用户名隐藏...")
            
            # 移动鼠标到其他位置
            page.mouse.move(0, 0)
            time.sleep(0.5)
            
            # 检查 nav-profile-text 再次隐藏
            is_hidden_again = page.evaluate('''() => {
                const el = document.getElementById('nav-profile-text');
                const style = window.getComputedStyle(el);
                return style.display === 'none';
            }''')
            
            if is_hidden_again:
                print("  ✓ 移开鼠标后用户名隐藏")
                results.append(("移开鼠标: 用户名隐藏", True, ""))
            else:
                print("  ✗ 移开鼠标后用户名未隐藏")
                results.append(("移开鼠标: 用户名隐藏", False, "用户名仍然可见"))
            
            # 截图：最终状态
            screenshot_path = os.path.join(SCREENSHOT_DIR, '86', 'profile_final.png')
            page.screenshot(path=screenshot_path)
            print(f"  截图保存: {screenshot_path}")
            
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