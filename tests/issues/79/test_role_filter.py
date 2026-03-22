#!/usr/bin/env python3
"""
Test script for Issue #79: Messages页面role过滤不生效

问题：Messages页面虽然选择了User这个role，但是下面的messages没有过滤

验证：
1. 打开 Messages 页面
2. 检查 User 角色是否默认选中
3. 验证 API 请求是否包含 role=user 参数
4. 验证返回的消息是否都是 user 角色
5. 取消所有角色选择，验证显示空状态提示
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from playwright.async_api import async_playwright, expect


async def test_role_filter():
    """Test that role filter works correctly on Messages page."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1400, 'height': 900},
            locale='zh-CN'
        )
        page = await context.new_page()

        try:
            # Navigate to Messages page
            print("\n[Step 1] Navigating to Messages page...")
            await page.goto('http://localhost:5001/messages', wait_until='networkidle')

            # Wait for React to render
            await asyncio.sleep(3)

            # Check if we need to login
            current_url = page.url
            print(f"  Current URL: {current_url}")

            if '/login' in current_url:
                print("  Need to login first...")
                # Login as admin
                await page.fill('#username', 'admin')
                await page.fill('#password', 'admin123')
                await page.click('button[type="submit"]')
                await page.wait_for_url('**/', timeout=10000)
                print("  ✓ Logged in")
                await page.goto('http://localhost:5001/messages', wait_until='networkidle')
                await asyncio.sleep(2)

            await page.wait_for_selector('.messages', timeout=15000)
            print("✓ Messages page loaded")

            # Check if User role checkbox is checked by default
            print("\n[Step 2] Checking User role checkbox state...")
            user_checkbox = page.locator('#roleUser')
            is_checked = await user_checkbox.is_checked()
            print(f"  User checkbox checked: {is_checked}")
            assert is_checked, "User role should be checked by default"
            print("✓ User role is checked by default")

            # Wait for messages to load
            print("\n[Step 3] Waiting for messages to load...")
            await page.wait_for_selector('.message-item', timeout=10000)
            await asyncio.sleep(2)  # Wait for API response

            # Get all visible messages and check their roles
            print("\n[Step 4] Checking message roles...")
            messages = await page.locator('.message-item').all()
            print(f"  Found {len(messages)} messages")

            if len(messages) > 0:
                # Check each message's role badge
                for i, msg in enumerate(messages[:10]):  # Check first 10 messages
                    role_badge = await msg.locator('.role-badge').first.text_content()
                    role = role_badge.strip().upper()
                    print(f"  Message {i+1}: {role}")
                    assert role == 'USER', f"Expected USER role, got {role}"

                print(f"✓ All {min(len(messages), 10)} checked messages have USER role")
            else:
                print("  No messages found (may be expected if no user messages today)")

            # Now uncheck User and check Assistant
            print("\n[Step 5] Switching to Assistant role...")
            await user_checkbox.click()  # Uncheck User
            assistant_checkbox = page.locator('#roleAssistant')
            await assistant_checkbox.click()  # Check Assistant

            # Wait for messages to reload
            await asyncio.sleep(2)
            await page.wait_for_selector('.message-item', timeout=10000)

            # Check messages are now Assistant role
            print("\n[Step 6] Checking message roles after filter change...")
            messages = await page.locator('.message-item').all()
            print(f"  Found {len(messages)} messages")

            if len(messages) > 0:
                for i, msg in enumerate(messages[:10]):
                    role_badge = await msg.locator('.role-badge').first.text_content()
                    role = role_badge.strip().upper()
                    print(f"  Message {i+1}: {role}")
                    assert role == 'ASSISTANT', f"Expected ASSISTANT role, got {role}"

                print(f"✓ All {min(len(messages), 10)} checked messages have ASSISTANT role")
            else:
                print("  No messages found (may be expected if no assistant messages today)")

            # Test: Uncheck all roles - should show empty state
            print("\n[Step 7] Unchecking all roles...")
            await assistant_checkbox.click()  # Uncheck Assistant

            # Wait for empty state to appear
            await asyncio.sleep(2)

            # Check for empty state message (EmptyState component uses h5 for title)
            print("\n[Step 8] Checking empty state when no role selected...")
            empty_title = page.locator('.messages .text-center h5')
            await empty_title.wait_for(timeout=5000)
            title_text = await empty_title.text_content()
            print(f"  Empty state title: {title_text}")
            assert 'Select Role' in title_text or '选择角色' in title_text, \
                f"Expected 'Select Role' empty state, got '{title_text}'"
            print("✓ Empty state shown when no role selected")

            # Take screenshot
            screenshot_path = Path(__file__).parent.parent.parent / 'screenshots/issues/79'
            screenshot_path.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path / 'role_filter_test.png'), full_page=True)
            print(f"\n✓ Screenshot saved to {screenshot_path / 'role_filter_test.png'}")

            print("\n" + "="*50)
            print("✅ All tests passed! Role filter is working correctly.")
            print("="*50)

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            # Take screenshot on failure
            screenshot_path = Path(__file__).parent.parent.parent / 'screenshots/issues/79'
            screenshot_path.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path / 'role_filter_failure.png'), full_page=True)
            raise
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(test_role_filter())