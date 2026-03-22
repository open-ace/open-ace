"""
UI Test for Issue 54: Management页面User Management的Add User页面没有Linux Account，Password应该提示确认一遍

测试步骤：
1. 登录系统
2. 进入 Management 页面
3. 点击 Users tab
4. 点击 Add User 按钮
5. 验证 Add User 模态框中存在 Linux Account 字段
6. 验证 Add User 模态框中存在 Confirm Password 字段
7. 验证密码不匹配时显示错误提示
"""

import pytest
from playwright.sync_api import expect
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))


async def take_screenshot(page, name):
    """Take a screenshot and return the path."""
    screenshot_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'screenshots', 'issues', '54')
    os.makedirs(screenshot_dir, exist_ok=True)
    path = os.path.join(screenshot_dir, name)
    await page.screenshot(path=path)
    return path


class TestIssue54:
    """Test class for Issue 54"""

    @pytest.mark.asyncio
    async def test_add_user_modal_fields(self):
        """Test Issue 54: Add User modal should have Linux Account and Confirm Password fields"""
        from playwright.async_api import async_playwright

        screenshots = []

        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                print("\n" + "=" * 60)
                print("UI Test: Issue 54 - Add User Modal Fields")
                print("=" * 60)

                # Step 1: Navigate to login page
                print("\n[Step 1] Navigate to login page")
                await page.goto('http://localhost:5001/')
                await page.wait_for_load_state('networkidle')
                screenshots.append(take_screenshot(page, '01_login_page.png'))
                print("  ✓ Login page loaded")

                # Step 2: Login as admin
                print("\n[Step 2] Login as admin")
                await page.fill('input[name="username"]', 'admin')
                await page.fill('input[name="password"]', 'admin123')
                await page.click('button[type="submit"]')
                await page.wait_for_load_state('networkidle')
                page.wait_for_timeout(1000)
                screenshots.append(take_screenshot(page, '02_after_login.png'))
                print("  ✓ Logged in as admin")

                # Step 3: Navigate to Management page
                print("\n[Step 3] Navigate to Management page")
                management_nav = await page.locator('#nav-management:visible')
                expect(management_nav).to_be_visible()
                management_nav.click()
                await page.wait_for_load_state('networkidle')
                page.wait_for_timeout(1000)
                
                # Wait for management section to be visible
                management_section = await page.locator('#management-section')
                expect(management_section).to_be_visible()
                screenshots.append(take_screenshot(page, '03_management_page.png'))
                print("  ✓ Management page loaded")

                # Step 4: Click Users tab
                print("\n[Step 4] Click Users tab")
                users_tab = await page.locator('#users-tab')
                expect(users_tab).to_be_visible()
                users_tab.click()
                page.wait_for_timeout(500)
                screenshots.append(take_screenshot(page, '04_users_tab.png'))
                print("  ✓ Users tab clicked")

                # Step 5: Click Add User button
                print("\n[Step 5] Click Add User button")
                add_user_btn = await page.locator('#add-user-btn')
                expect(add_user_btn).to_be_visible()
                expect(add_user_btn).to_be_enabled()
                add_user_btn.click()
                page.wait_for_timeout(500)
                screenshots.append(take_screenshot(page, '05_add_user_modal.png'))
                print("  ✓ Add User modal opened")

                # Step 6: Verify Linux Account field exists
                print("\n[Step 6] Verify Linux Account field exists")
                linux_account_input = await page.locator('#add-linux-account')
                expect(linux_account_input).to_be_visible()
                print("  ✓ Linux Account field is visible")

                # Verify Linux Account label
                linux_account_label = await page.locator('label[for="add-linux-account"]')
                expect(linux_account_label).to_contain_text('Linux Account')
                print("  ✓ Linux Account label is correct")

                # Verify Linux Account hint text
                linux_account_hint = await page.locator('#add-linux-account + small.form-text')
                expect(linux_account_hint).to_be_visible()
                print("  ✓ Linux Account hint text is visible")

                # Step 7: Verify Confirm Password field exists
                print("\n[Step 7] Verify Confirm Password field exists")
                confirm_password_input = await page.locator('#add-confirm-password')
                expect(confirm_password_input).to_be_visible()
                print("  ✓ Confirm Password field is visible")

                # Verify Confirm Password label
                confirm_password_label = await page.locator('label[for="add-confirm-password"]')
                expect(confirm_password_label).to_contain_text('Confirm Password')
                print("  ✓ Confirm Password label is correct")

                # Step 8: Test password mismatch validation
                print("\n[Step 8] Test password mismatch validation")
                password_input = await page.locator('#add-password')
                password_input.fill('password123')
                confirm_password_input.fill('password456')
                # Trigger validation by clicking outside or on another field
                await page.locator('#add-username').click()
                page.wait_for_timeout(300)

                # Check if error message is displayed
                password_match_msg = await page.locator('#password-match-msg')
                expect(password_match_msg).to_be_visible()
                expect(password_match_msg).to_contain_text('Passwords do not match')
                print("  ✓ Password mismatch error message is displayed")

                # Step 9: Test password match removes error
                print("\n[Step 9] Test password match removes error")
                confirm_password_input.fill('password123')
                await page.locator('#add-username').click()
                page.wait_for_timeout(300)
                expect(password_match_msg).to_be_hidden()
                print("  ✓ Password match removes error message")

                screenshots.append(take_screenshot(page, '06_password_validation.png'))

                # Close modal
                print("\n[Step 10] Close modal")
                await page.click('#addUserModal .btn-close')
                page.wait_for_timeout(500)

                # Logout
                print("\n[Step 11] Logout")
                logout_btn = await page.locator('a:has-text("Logout")')
                if logout_btn.is_visible():
                    logout_btn.click()
                    page.wait_for_timeout(500)

                print("\n" + "=" * 60)
                print("✓ All tests passed!")
                print("=" * 60)
                print("\nScreenshots saved:")
                for s in screenshots:
                    print(f"  - {s}")

            except Exception as e:
                screenshots.append(take_screenshot(page, 'error.png'))
                print(f"\n✗ Test failed: {e}")
                raise

            finally:
                await browser.close()


if __name__ == '__main__':
    test = TestIssue54()
    test.test_add_user_modal_fields()