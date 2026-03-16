#!/usr/bin/env python3
"""
Test script for Issue #24: Messages 页面 qwen 工具的 user 消息显示 OPENCLAW 标签

验证修复：qwen 工具的 user 消息应该显示 qwen 标签，而不是 OPENCLAW 标签
"""

import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots')


def ensure_screenshot_dir():
    """Ensure screenshot directory exists"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_issue24():
    """Test that qwen tool user messages show 'qwen' label, not 'openclaw'"""
    ensure_screenshot_dir()
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()
        
        try:
            print("=" * 60)
            print("测试 Issue #24: qwen 工具 user 消息标签显示")
            print("=" * 60)
            
            # Step 1: Navigate to login page
            print("\n[Step 1] 导航到登录页面...")
            page.goto(f"{BASE_URL}/")
            page.wait_for_load_state('networkidle')
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_01_login.png'))
            
            # Step 2: Login
            print("[Step 2] 登录系统...")
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle')
            time.sleep(2)  # Wait for redirect after login
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_02_dashboard.png'))
            
            # Debug: print current URL and check if logged in
            print(f"  当前 URL: {page.url}")
            
            # Check if we're still on login page (login failed)
            if 'login' in page.url or page.locator('input[name="username"]').count() > 0:
                print("  警告: 登录可能失败，仍在登录页面")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_02_login_failed.png'))
                return False
            
            # Step 3: Navigate to Messages page
            print("[Step 3] 导航到 Messages 页面...")
            # Wait for the sidebar to be ready
            time.sleep(2)
            messages_nav = page.locator('#nav-messages')
            if messages_nav.count() == 0:
                print("  警告: 未找到 #nav-messages，尝试其他选择器...")
                # Try alternative selector
                messages_nav = page.locator('a:has-text("Messages")')
            
            if messages_nav.count() > 0:
                messages_nav.click()
            else:
                print("  错误: 无法找到 Messages 导航链接")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_03_error.png'))
                return False
            
            page.wait_for_load_state('networkidle')
            time.sleep(2)  # Wait for messages to load
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_03_messages.png'))
            
            # Step 4: Filter by qwen tool
            print("[Step 4] 筛选 qwen 工具消息...")
            
            # Check if tool filter exists and select qwen
            tool_select = page.locator('#tool-filter')
            if tool_select.count() > 0:
                tool_select.select_option('qwen')
                page.wait_for_load_state('networkidle')
                time.sleep(2)
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_04_qwen_filter.png'))
            else:
                print("  警告: 未找到工具筛选器，尝试其他方式...")
            
            # Step 5: Check user messages for qwen label
            print("[Step 5] 检查 user 消息的标签...")
            
            # Find all user messages
            user_messages = page.locator('.message-item').all()
            print(f"  找到 {len(user_messages)} 条消息")
            
            qwen_user_messages = 0
            openclaw_user_messages = 0
            test_passed = True
            
            for i, msg in enumerate(user_messages[:10]):  # Check first 10 messages
                # Check if it's a user message
                role_badge = msg.locator('.role-badge.user')
                if role_badge.count() > 0:
                    # It's a user message, check the source label
                    source_label = msg.locator('.message-source')
                    if source_label.count() > 0:
                        source_text = source_label.inner_text().strip().lower()
                        print(f"  消息 {i+1}: role=user, source={source_text}")
                        
                        # Check if tool_name is shown in the message meta
                        tool_name_elem = msg.locator('.message-meta .text-muted:has(.bi-box-seam)')
                        
                        if source_text == 'qwen':
                            qwen_user_messages += 1
                            print(f"    ✓ 正确显示 qwen 标签")
                        elif source_text == 'openclaw':
                            openclaw_user_messages += 1
                            print(f"    ✗ 错误显示 openclaw 标签（应该显示 qwen）")
                            test_passed = False
                        else:
                            print(f"    ? 显示其他标签: {source_text}")
            
            # Take final screenshot
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, 'issue24_05_result.png'))
            
            # Print summary
            print("\n" + "=" * 60)
            print("测试结果汇总")
            print("=" * 60)
            print(f"qwen user 消息显示 qwen 标签: {qwen_user_messages} 条")
            print(f"qwen user 消息显示 openclaw 标签: {openclaw_user_messages} 条")
            
            if test_passed and qwen_user_messages > 0:
                print("\n✓ 测试通过: qwen 工具的 user 消息正确显示 qwen 标签")
            elif qwen_user_messages == 0 and openclaw_user_messages == 0:
                print("\n⚠ 警告: 未找到 qwen user 消息进行测试")
            else:
                print("\n✗ 测试失败: qwen 工具的 user 消息错误显示 openclaw 标签")
            
            print("=" * 60)
            
            return test_passed
            
        finally:
            browser.close()


if __name__ == '__main__':
    success = test_issue24()
    sys.exit(0 if success else 1)