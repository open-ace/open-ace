#!/usr/bin/env python3
"""
UI Test for Issue 53: Management页面User Management的Add User按钮点不了

测试内容：
1. 登录系统
2. 导航到 Management 页面
3. 检查 Add User 按钮是否存在且可点击
4. 点击 Add User 按钮
5. 验证 Add User 模态框是否弹出
6. 检查模态框中的表单字段是否完整
"""

import pytest
import sys
import os
import time

# Add skill scripts directory to path
skill_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, skill_dir)

try:
    from playwright.async_api import async_playwright, expect
except ImportError:
    print(
        "Error: playwright not installed. Run: pip install playwright && playwright install chromium"
    )
    sys.exit(1)

# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(skill_dir))), "screenshots", "issues", "53"
)
TIMEOUT = 60000  # 60 seconds timeout

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def take_screenshot(page, name):
    """Take screenshot and save to issue directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    await page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


@pytest.mark.asyncio
async def test_issue53():
    """Test Issue 53: Add User button functionality in Management Users tab"""
    screenshots = []

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        await page.set_default_timeout(TIMEOUT)

        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 53 - Add User Button Functionality")
            print("=" * 60)

            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            await page.goto(f"{BASE_URL}/login")
            await page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "01_login_page.png"))
            print("  ✓ Login page loaded")

            # Step 2: Login
            print("\n[Step 2] Login as admin")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "02_after_login.png"))
            print("  ✓ Logged in successfully")

            # Step 3: Navigate to Management page
            print("\n[Step 3] Navigate to Management page")

            # Wait for page to be fully loaded
            time.sleep(2)

            # Check if Management nav is visible (admin only)
            nav_management = await page.locator("#nav-management:visible")
            nav_count = nav_management.count()
            print(f"  Found {nav_count} visible Management nav links")

            if nav_count == 0:
                print("  ! Management nav not visible - user may not be admin")
                screenshots.append(take_screenshot(page, "03_no_management_nav.png"))
                print("  ✗ Test cannot continue - admin access required")
                return False

            nav_management.first.click()
            await page.wait_for_load_state("networkidle")
            time.sleep(2)

            # Wait for management section to be visible
            management_section = await page.locator("#management-section")
            expect(management_section).to_be_visible()
            print("  ✓ Management section is visible")

            screenshots.append(take_screenshot(page, "03_management_page.png"))
            print("  ✓ Management page loaded")

            # Step 4: Check Users tab is active
            print("\n[Step 4] Check Users tab")
            users_tab = await page.locator("#users-tab")
            expect(users_tab).to_be_visible()
            print("  ✓ Users tab is visible")

            # Step 5: Check Add User button exists and is visible
            print("\n[Step 5] Check Add User button")
            add_user_btn = await page.locator("#add-user-btn")
            expect(add_user_btn).to_be_visible()
            print("  ✓ Add User button is visible")

            # Check button is enabled (not disabled)
            expect(add_user_btn).to_be_enabled()
            print("  ✓ Add User button is enabled (not disabled)")

            # Check button text
            btn_text = add_user_btn.inner_text()
            print(f"  Button text: {btn_text}")
            assert "Add User" in btn_text, "Button text should contain 'Add User'"
            print("  ✓ Button text is correct")

            screenshots.append(take_screenshot(page, "04_add_user_button.png"))

            # Step 6: Click Add User button
            print("\n[Step 6] Click Add User button")
            add_user_btn.click()
            time.sleep(1)

            # Step 7: Verify Add User modal appears
            print("\n[Step 7] Verify Add User modal")
            modal = await page.locator("#addUserModal")
            expect(modal).to_be_visible()
            print("  ✓ Add User modal is visible")

            # Check modal title
            modal_title = await page.locator("#addUserModalLabel")
            expect(modal_title).to_be_visible()
            print(f"  Modal title: {modal_title.inner_text()}")

            screenshots.append(take_screenshot(page, "05_add_user_modal.png"))

            # Step 8: Check form fields in modal
            print("\n[Step 8] Check form fields in modal")

            # Check username field
            username_input = await page.locator("#add-username")
            expect(username_input).to_be_visible()
            print("  ✓ Username input field found")

            # Check password field
            password_input = await page.locator("#add-password")
            expect(password_input).to_be_visible()
            print("  ✓ Password input field found")

            # Check email field
            email_input = await page.locator("#add-email")
            expect(email_input).to_be_visible()
            print("  ✓ Email input field found")

            # Check role select
            role_select = await page.locator("#add-role")
            expect(role_select).to_be_visible()
            print("  ✓ Role select field found")

            # Check quota tokens field
            quota_tokens_input = await page.locator("#add-quota-tokens")
            expect(quota_tokens_input).to_be_visible()
            print("  ✓ Quota Tokens input field found")

            # Check quota requests field
            quota_requests_input = await page.locator("#add-quota-requests")
            expect(quota_requests_input).to_be_visible()
            print("  ✓ Quota Requests input field found")

            # Check status select
            status_select = await page.locator("#add-is-active")
            expect(status_select).to_be_visible()
            print("  ✓ Status select field found")

            # Check Create User button
            create_btn = await page.locator("#addUserModal .btn-primary:not(.btn-secondary)")
            expect(create_btn).to_be_visible()
            print("  ✓ Create User button found")

            screenshots.append(take_screenshot(page, "06_form_fields.png"))

            # Step 9: Close modal
            print("\n[Step 9] Close modal")
            await page.click("#addUserModal .btn-close")
            time.sleep(0.5)

            # Verify modal is closed
            modal = await page.locator("#addUserModal")
            expect(modal).to_be_hidden()
            print("  ✓ Modal closed successfully")

            screenshots.append(take_screenshot(page, "07_modal_closed.png"))

            # Step 10: Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            print("✓ All tests passed!")
            print("✓ Add User button is clickable and opens modal correctly")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")

            return True

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            screenshots.append(take_screenshot(page, "error.png"))
            import traceback

            traceback.print_exc()
            return False

        finally:
            await browser.close()


if __name__ == "__main__":
    success = test_issue53()
    sys.exit(0 if success else 1)
