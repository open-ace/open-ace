#!/usr/bin/env python3
"""
UI Test for Issue 51: 普通用户登录后应该直接进入 Workspace 页面

测试内容：
1. 创建普通用户（如果不存在）
2. 普通用户登录
3. 验证 Dashboard 导航链接不可见（仅管理员可见）
4. 验证 Workspace section 默认显示
5. 验证 Workspace 导航链接可见
6. 验证 admin-only 菜单隐藏
"""

import sys
import os
import time

# Add skill scripts directory to path
skill_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, skill_dir)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
ADMIN_USERNAME = os.environ.get('TEST_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
TEST_USER_USERNAME = 'testuser'
TEST_USER_PASSWORD = 'testpass123'
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(skill_dir))), 'screenshots', 'issues', '51')
TIMEOUT = 60000  # 60 seconds timeout

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take screenshot and save to issue directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_issue51():
    """Test Issue 51: Normal user should see Workspace after login (not Dashboard)"""
    screenshots = []

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 51 - Normal User Workspace Display")
            print("=" * 60)

            # Step 1: Login as admin to create test user
            print("\n[Step 1] Login as admin to create test user")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')
            page.fill('input[name="username"]', ADMIN_USERNAME)
            page.fill('input[name="password"]', ADMIN_PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            screenshots.append(take_screenshot(page, '01_admin_login.png'))
            print("  ✓ Admin logged in successfully")

            # Step 2: Navigate to Management page to create user
            print("\n[Step 2] Navigate to Management page")
            page.click('#nav-management')
            page.wait_for_load_state('networkidle')
            time.sleep(1)
            screenshots.append(take_screenshot(page, '02_management_page.png'))
            print("  ✓ Management page loaded")

            # Step 3: Check if test user exists, if not create it
            print("\n[Step 3] Check/Create test user")
            page.wait_for_selector('#users-table-body tr', timeout=5000)
            rows = page.locator('#users-table-body tr').all()
            user_exists = False
            for row in rows:
                if TEST_USER_USERNAME in row.inner_text():
                    user_exists = True
                    break

            if not user_exists:
                print(f"  Creating test user: {TEST_USER_USERNAME}")
                # Click Add User button
                page.click('#add-user-btn')
                time.sleep(1)

                # Fill in user details
                page.fill('#add-username', TEST_USER_USERNAME)
                page.fill('#add-password', TEST_USER_PASSWORD)
                page.fill('#add-confirm-password', TEST_USER_PASSWORD)

                # Set role to user (not admin)
                page.select_option('#add-role', 'user')

                # Click Create button
                page.click('#addUserModal .btn-primary')
                time.sleep(1)
                screenshots.append(take_screenshot(page, '03_create_user.png'))
                print("  ✓ Test user created")
            else:
                print(f"  Test user {TEST_USER_USERNAME} already exists")

            # Step 4: Logout admin
            print("\n[Step 4] Logout admin")
            page.click('#nav-logout')
            page.wait_for_load_state('networkidle')
            time.sleep(1)
            screenshots.append(take_screenshot(page, '04_logout.png'))
            print("  ✓ Admin logged out")

            # Step 5: Login as test user
            print("\n[Step 5] Login as test user (normal user)")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')
            page.fill('input[name="username"]', TEST_USER_USERNAME)
            page.fill('input[name="password"]', TEST_USER_PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            screenshots.append(take_screenshot(page, '05_normal_user_login.png'))
            print("  ✓ Normal user logged in successfully")

            # Step 6: Verify Dashboard navigation link is NOT visible (admin only)
            print("\n[Step 6] Verify Dashboard navigation link is NOT visible (admin only)")
            dashboard_nav = page.locator('#nav-dashboard')
            expect(dashboard_nav).not_to_be_visible()
            print("  ✓ Dashboard navigation link is hidden (admin only)")

            # Step 7: Verify Workspace section is displayed by default
            print("\n[Step 7] Verify Workspace section is displayed by default")
            workspace_section = page.locator('#workspace-section')
            expect(workspace_section).to_be_visible()
            print("  ✓ Workspace section is displayed by default")

            # Step 8: Verify Workspace navigation link is visible
            print("\n[Step 8] Verify Workspace navigation link is visible")
            workspace_nav = page.locator('#nav-workspace')
            expect(workspace_nav).to_be_visible()
            print("  ✓ Workspace navigation link is visible")

            # Step 9: Verify Dashboard section is NOT displayed
            print("\n[Step 9] Verify Dashboard section is NOT displayed")
            dashboard_section = page.locator('#dashboard-section')
            expect(dashboard_section).not_to_be_visible()
            print("  ✓ Dashboard section is hidden")

            # Step 10: Verify admin-only menus are hidden
            print("\n[Step 10] Verify admin-only menus are hidden")
            messages_nav = page.locator('#nav-messages')
            analysis_nav = page.locator('#nav-analysis')
            management_nav = page.locator('#nav-management')

            # These should be hidden for normal user
            expect(messages_nav).not_to_be_visible()
            expect(analysis_nav).not_to_be_visible()
            expect(management_nav).not_to_be_visible()
            print("  ✓ Admin-only menus (Messages, Analysis, Management) are hidden")

            # Step 11: Take final screenshot
            print("\n[Step 11] Take final screenshot")
            screenshots.append(take_screenshot(page, '06_final_workspace.png'))

            # Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            print("✓ All tests passed!")
            print("\nVerified:")
            print("  - Dashboard navigation link is hidden for normal user (admin only)")
            print("  - Workspace section is displayed by default after login")
            print("  - Workspace navigation link is visible")
            print("  - Dashboard section is hidden")
            print("  - Admin-only menus are hidden for normal user")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")

            return True

        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            screenshots.append(take_screenshot(page, 'error.png'))
            return False

        finally:
            browser.close()


if __name__ == '__main__':
    success = test_issue51()
    sys.exit(0 if success else 1)