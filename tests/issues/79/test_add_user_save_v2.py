#!/usr/bin/env python3
"""
UI Test for Issue 79: User Management页面点Add User弹出的对话框点Save没有反应
Version 2: 更精确的表单填写
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/79"


async def test_add_user_save():
    """测试 Add User 对话框的 Save 按钮"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})

        page = await context.new_page()

        # 收集控制台消息
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        try:
            print("=" * 60)
            print("UI Test: Issue 79 - Add User Save Button (v2)")
            print("=" * 60)

            # Step 1: 访问首页并登录
            print("\n[Step 1] Navigate and login")
            await page.goto(BASE_URL, wait_until="networkidle")
            await page.wait_for_timeout(1000)

            # 检查是否需要登录
            if "/login" in page.url or "login" in page.url:
                await page.fill("#username", "admin")
                await page.fill("#password", "admin123")
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(2000)
                print("  ✓ Logged in")

            # Step 2: 进入 Management 页面
            print("\n[Step 2] Navigate to Management page")
            await page.goto(f"{BASE_URL}/management", wait_until="networkidle")
            await page.wait_for_timeout(1000)

            # 点击 User Management 标签
            user_tab = page.locator('button, [role="tab"]').filter(has_text="User")
            if await user_tab.count() > 0:
                await user_tab.first.click()
                await page.wait_for_timeout(500)

            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_01_user_management.png")
            print("  ✓ User Management tab loaded")

            # Step 3: 点击 Add User 按钮
            print("\n[Step 3] Click Add User button")
            add_user_btn = page.locator("button").filter(has_text="Add User")
            await add_user_btn.first.click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_02_add_user_modal.png")
            print("  ✓ Add User modal opened")

            # Step 4: 测试短密码 - 应该显示错误
            print("\n[Step 4] Test short password validation")

            # 使用 label 定位输入框
            await page.locator(
                'label:has-text("Username") + input, input[placeholder*="username"]'
            ).first.fill("testuser79")
            await page.locator('label:has-text("Email") + input, input[type="email"]').first.fill(
                "test79@test.com"
            )

            # 填写短密码
            password_inputs = page.locator('input[type="password"]')
            await password_inputs.first.fill("short")
            if await password_inputs.count() >= 2:
                await password_inputs.nth(1).fill("short")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_03_short_password.png")

            # 点击 Save
            await page.locator("button").filter(has_text="Save").first.click()
            await page.wait_for_timeout(1000)

            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_04_after_save_short.png")

            # 检查错误信息
            error_alert = page.locator(".alert-danger")
            if await error_alert.count() > 0:
                error_text = await error_alert.first.text_content()
                print(f"  ✓ Error message displayed: {error_text}")
            else:
                print("  ✗ No error message found!")

            # Step 5: 测试有效数据
            print("\n[Step 5] Test valid data")

            # 关闭 modal
            await page.locator("button").filter(has_text="Cancel").first.click()
            await page.wait_for_timeout(500)

            # 重新打开
            await page.locator("button").filter(has_text="Add User").first.click()
            await page.wait_for_timeout(500)

            # 填写有效数据
            await page.locator(
                'label:has-text("Username") + input, input[placeholder*="username"]'
            ).first.fill("testuser79_valid")
            await page.locator('label:has-text("Email") + input, input[type="email"]').first.fill(
                "test79_valid@test.com"
            )

            # 填写有效密码
            password_inputs = page.locator('input[type="password"]')
            await password_inputs.first.fill("validpass123")
            if await password_inputs.count() >= 2:
                await password_inputs.nth(1).fill("validpass123")

            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_05_valid_data.png")

            # 点击 Save
            await page.locator("button").filter(has_text="Save").first.click()
            await page.wait_for_timeout(2000)

            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_06_after_save_valid.png")

            # 检查 modal 是否关闭
            modal = page.locator('.modal.show, [role="dialog"]')
            modal_visible = await modal.count() > 0 and await modal.first.is_visible()

            if modal_visible:
                print("  ! Modal still open - checking for errors")
                error_alert = page.locator(".alert-danger")
                if await error_alert.count() > 0:
                    error_text = await error_alert.first.text_content()
                    print(f"  ! Error: {error_text}")
            else:
                print("  ✓ Modal closed - user should be created")

            # 检查用户列表
            await page.wait_for_timeout(1000)
            user_row = page.locator("tr").filter(has_text="testuser79_valid")
            if await user_row.count() > 0:
                print("  ✓ User 'testuser79_valid' found in table!")
            else:
                print("  ! User 'testuser79_valid' not found in table")

            # 打印控制台消息
            if console_messages:
                print("\n[Console Messages]")
                for msg in console_messages[-10:]:
                    print(f"  {msg}")

            print("\n" + "=" * 60)
            print("Test completed!")
            print("=" * 60)

            return True

        except Exception as e:
            print(f"\n✗ Error: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/v2_error.png")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(test_add_user_save())
    sys.exit(0 if result else 1)
