#!/usr/bin/env python3
"""
UI Test for Issue 79: User Management页面点Add User弹出的对话框点Save没有反应

测试步骤：
1. 登录系统
2. 进入 Management 页面
3. 点击 User Management 标签
4. 点击 Add User 按钮
5. 填写表单（使用短密码触发验证错误）
6. 点击 Save 按钮
7. 检查是否有错误提示
8. 填写正确表单
9. 点击 Save 按钮
10. 检查是否成功创建用户
"""

import asyncio
from playwright.async_api import async_playwright
import os
import sys

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
SESSION_TOKEN = os.environ.get("SESSION_TOKEN", "")
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/79"


async def test_add_user_save():
    """测试 Add User 对话框的 Save 按钮"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})

        # 如果有 session token，设置 cookie
        if SESSION_TOKEN:
            await context.add_cookies(
                [
                    {
                        "name": "session_token",
                        "value": SESSION_TOKEN,
                        "domain": "localhost",
                        "path": "/",
                    }
                ]
            )

        page = await context.new_page()

        try:
            print("=" * 60)
            print("UI Test: Issue 79 - Add User Save Button Not Working")
            print("=" * 60)

            # Step 1: 访问首页
            print("\n[Step 1] Navigate to home page")
            await page.goto(BASE_URL, wait_until="networkidle")
            await page.wait_for_timeout(1000)

            # 检查是否需要登录
            current_url = page.url
            if "/login" in current_url or "login" in current_url:
                print("  Need to login first...")
                # 使用测试账号登录
                await page.fill("#username", "admin")
                await page.fill("#password", "admin123")
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(2000)
                print("  ✓ Logged in")

            # Step 2: 进入 Management 页面
            print("\n[Step 2] Navigate to Management page")
            await page.goto(f"{BASE_URL}/management", wait_until="networkidle")
            await page.wait_for_timeout(1000)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_management_page.png")
            print("  ✓ Management page loaded")

            # Step 3: 点击 User Management 标签
            print("\n[Step 3] Click User Management tab")
            # 查找 User Management 标签
            user_tab = page.locator('button, [role="tab"]').filter(has_text="User")
            if await user_tab.count() > 0:
                await user_tab.first.click()
                await page.wait_for_timeout(500)
                print("  ✓ User Management tab clicked")
            else:
                print("  ! User Management tab not found, might already be on it")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_user_management.png")

            # Step 4: 点击 Add User 按钮
            print("\n[Step 4] Click Add User button")
            add_user_btn = page.locator("button").filter(has_text="Add User")
            if await add_user_btn.count() > 0:
                await add_user_btn.first.click()
                await page.wait_for_timeout(500)
                print("  ✓ Add User button clicked")
            else:
                print("  ✗ Add User button not found!")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/03_no_add_user_button.png")
                return False

            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_add_user_modal.png")

            # Step 5: 填写表单（使用短密码触发验证错误）
            print("\n[Step 5] Fill form with short password")

            # 填写用户名
            username_input = page.locator(
                'input[name="username"], input[placeholder*="username"], input[placeholder*="用户名"]'
            ).first
            if await username_input.count() > 0:
                await username_input.fill("testuser79")
                print("  ✓ Username filled: testuser79")
            else:
                # 尝试通过 label 查找
                inputs = page.locator('input[type="text"], input:not([type])')
                await inputs.first.fill("testuser79")
                print("  ✓ Username filled (via generic input)")

            # 填写邮箱
            email_input = page.locator(
                'input[type="email"], input[placeholder*="email"], input[placeholder*="邮箱"]'
            ).first
            if await email_input.count() > 0:
                await email_input.fill("testuser79@test.com")
                print("  ✓ Email filled: testuser79@test.com")
            else:
                inputs = page.locator('input[type="text"], input:not([type])')
                if await inputs.count() >= 2:
                    await inputs.nth(1).fill("testuser79@test.com")
                    print("  ✓ Email filled (via second input)")

            # 填写短密码（应该触发验证错误）
            password_inputs = page.locator('input[type="password"]')
            if await password_inputs.count() >= 1:
                await password_inputs.first.fill("short")  # 短密码
                print("  ✓ Password filled: short (should fail validation)")
            if await password_inputs.count() >= 2:
                await password_inputs.nth(1).fill("short")  # 确认密码
                print("  ✓ Confirm password filled: short")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/05_form_filled_short_password.png")

            # Step 6: 点击 Save 按钮
            print("\n[Step 6] Click Save button with short password")
            save_btn = page.locator("button").filter(has_text="Save")
            if await save_btn.count() > 0:
                await save_btn.first.click()
                await page.wait_for_timeout(2000)  # 等待响应
                print("  ✓ Save button clicked")
            else:
                print("  ✗ Save button not found!")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/06_no_save_button.png")
                return False

            await page.screenshot(path=f"{SCREENSHOT_DIR}/07_after_save_short_password.png")

            # 检查是否有错误提示
            print("\n[Step 7] Check for error message")

            # 检查 alert
            dialog_handled = False

            async def handle_dialog(dialog):
                nonlocal dialog_handled
                print(f"  ! Dialog appeared: {dialog.message}")
                dialog_handled = True
                await dialog.dismiss()

            page.on("dialog", handle_dialog)

            # 检查页面上的错误信息
            error_elements = page.locator(
                '.alert-danger, .error, [class*="error"], [class*="alert"]'
            )
            error_count = await error_elements.count()

            if error_count > 0:
                for i in range(error_count):
                    error_text = await error_elements.nth(i).text_content()
                    print(f"  ! Error message found: {error_text}")
            else:
                print("  ! No visible error message found on page")

            # 检查 modal 是否仍然打开
            modal = page.locator('.modal, [role="dialog"]')
            if await modal.count() > 0 and await modal.first.is_visible():
                print("  ! Modal is still open (Save did not close it)")
            else:
                print("  ✓ Modal closed")

            # 检查控制台错误
            print("\n[Step 8] Check console for errors")
            # 这需要在页面加载前设置监听器，这里我们只是提示

            # Step 9: 重新打开 modal，填写正确表单
            print("\n[Step 9] Re-open modal and fill with valid data")

            # 如果 modal 还开着，先关闭
            close_btn = page.locator("button").filter(has_text="Cancel")
            if await close_btn.count() > 0:
                await close_btn.first.click()
                await page.wait_for_timeout(500)

            # 重新打开 Add User
            add_user_btn = page.locator("button").filter(has_text="Add User")
            if await add_user_btn.count() > 0:
                await add_user_btn.first.click()
                await page.wait_for_timeout(500)

            # 填写正确数据
            inputs = page.locator('input[type="text"], input:not([type])')
            await inputs.first.fill("testuser79_valid")

            if await inputs.count() >= 2:
                await inputs.nth(1).fill("testuser79@test.com")

            # 填写有效密码（至少8位）
            password_inputs = page.locator('input[type="password"]')
            if await password_inputs.count() >= 1:
                await password_inputs.first.fill("validpass123")
            if await password_inputs.count() >= 2:
                await password_inputs.nth(1).fill("validpass123")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/08_form_filled_valid.png")

            # 点击 Save
            print("\n[Step 10] Click Save with valid data")
            save_btn = page.locator("button").filter(has_text="Save")
            if await save_btn.count() > 0:
                await save_btn.first.click()
                await page.wait_for_timeout(2000)

            await page.screenshot(path=f"{SCREENSHOT_DIR}/09_after_save_valid.png")

            # 检查 modal 是否关闭
            modal = page.locator('.modal, [role="dialog"]')
            if await modal.count() > 0 and await modal.first.is_visible():
                print("  ! Modal is still open after valid save")
            else:
                print("  ✓ Modal closed after valid save")

            # 检查用户是否创建成功
            print("\n[Step 11] Check if user was created")
            user_row = page.locator("tr").filter(has_text="testuser79_valid")
            if await user_row.count() > 0:
                print("  ✓ User 'testuser79_valid' found in table!")
            else:
                print("  ! User 'testuser79_valid' not found in table")

            print("\n" + "=" * 60)
            print("Test completed. Check screenshots for details.")
            print(f"Screenshots saved to: {SCREENSHOT_DIR}")
            print("=" * 60)

            return True

        except Exception as e:
            print(f"\n✗ Error: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error.png")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_add_user_save())
    sys.exit(0 if result else 1)
