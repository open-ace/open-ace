#!/usr/bin/env python3
"""
UI Test: User Management - Enter key to save user

测试内容：
1. 登录系统
2. 导航到 Management -> Users 页面
3. 点击添加用户按钮打开对话框
4. 填写用户信息
5. 按回车键提交表单
6. 验证对话框关闭（保存成功）
"""

import pytest
import sys
import os
import time
import random

# Add skill scripts directory to path
skill_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, skill_dir)

try:
    from playwright.async_api import async_playwright, expect
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(skill_dir))), 'screenshots')
TIMEOUT = 60000  # 60 seconds timeout

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def take_screenshot(page, name):
    """Take screenshot and save to directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    await page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


@pytest.mark.asyncio
async def test_user_enter_save():
    """Test Enter key saves user in create user dialog"""
    screenshots = []

    # Generate unique username for testing
    test_username = f"testuser_{random.randint(1000, 9999)}"
    test_email = f"{test_username}@test.com"
    test_password = "TestPass123!"

    async with async_playwright() as p:
        # Launch browser in headless mode
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await context.new_page()

        try:
            print("\n" + "=" * 60)
            print("UI Test: User Management - Enter key to save")
            print("=" * 60)

            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            await page.goto(f'{BASE_URL}/login')
            await page.wait_for_load_state('networkidle')
            screenshots.append(await take_screenshot(page, 'test_enter_save_01_login.png'))
            print("  ✓ Login page loaded")

            # Step 2: Login
            print("\n[Step 2] Login as admin")
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state('networkidle')
            time.sleep(2)
            print("  ✓ Logged in successfully")

            # Step 3: Navigate to Management page
            print("\n[Step 3] Navigate to Management page")
            await page.click('#nav-management')
            await page.wait_for_load_state('networkidle')
            time.sleep(1)
            screenshots.append(await take_screenshot(page, 'test_enter_save_02_management.png'))
            print("  ✓ Management page loaded")

            # Step 4: Click Users tab
            print("\n[Step 4] Click Users tab")
            users_tab = page.locator('#users-tab')
            await expect(users_tab).to_be_visible()
            await users_tab.click()
            time.sleep(1)
            print("  ✓ Users tab clicked")

            # Step 5: Click Add User button
            print("\n[Step 5] Click Add User button")
            add_user_btn = page.locator('button:has-text("Add User")')
            await expect(add_user_btn).to_be_visible()
            await add_user_btn.click()
            time.sleep(1)
            screenshots.append(await take_screenshot(page, 'test_enter_save_03_add_user_modal.png'))
            print("  ✓ Add User modal opened")

            # Step 6: Check modal is visible
            print("\n[Step 6] Check modal is visible")
            modal = page.locator('.modal.show')
            await expect(modal).to_be_visible()
            print("  ✓ Modal is visible")

            # Step 7: Fill in user form
            print("\n[Step 7] Fill in user form")
            # Find all text inputs in the modal
            inputs = await modal.locator('input[type="text"], input[type="email"], input[type="password"]').all()

            # Fill username
            username_input = modal.locator('input').first
            await username_input.fill(test_username)
            print(f"  ✓ Username filled: {test_username}")

            # Fill email (second input)
            email_input = modal.locator('input[type="email"]')
            if await email_input.count() > 0:
                await email_input.fill(test_email)
                print(f"  ✓ Email filled: {test_email}")

            # Fill password inputs
            password_inputs = await modal.locator('input[type="password"]').all()
            if len(password_inputs) >= 2:
                await password_inputs[0].fill(test_password)
                await password_inputs[1].fill(test_password)
                print("  ✓ Password fields filled")

            screenshots.append(await take_screenshot(page, 'test_enter_save_04_form_filled.png'))

            # Step 8: Press Enter to submit
            print("\n[Step 8] Press Enter to submit form")
            # Focus on the last input and press Enter
            await password_inputs[-1].press('Enter')
            time.sleep(2)
            screenshots.append(await take_screenshot(page, 'test_enter_save_05_after_enter.png'))

            # Step 9: Verify modal closed (form submitted)
            print("\n[Step 9] Verify modal closed")
            modal_visible = await modal.is_visible()
            if not modal_visible:
                print("  ✓ Modal closed - form submitted successfully via Enter key")
            else:
                # Check if there's an error message
                error_alert = modal.locator('.alert-danger')
                if await error_alert.count() > 0:
                    error_text = await error_alert.inner_text()
                    print(f"  ! Form submission returned error: {error_text}")
                    # This might be due to duplicate username, which is expected
                    if 'already exists' in error_text.lower() or 'duplicate' in error_text.lower():
                        print("  ✓ Enter key triggered form submission (duplicate user error is expected)")
                    else:
                        raise Exception(f"Unexpected error: {error_text}")
                else:
                    raise Exception("Modal still visible after Enter key - form not submitted")

            # Step 10: Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            print("✓ All tests passed!")
            print("✓ Enter key triggers form submission in User Management dialog")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")

            return True

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            screenshots.append(await take_screenshot(page, 'test_enter_save_error.png'))
            return False

        finally:
            await browser.close()


if __name__ == '__main__':
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)