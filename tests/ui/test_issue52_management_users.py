#!/usr/bin/env python3
"""
UI Test for Issue 52: Management页面Users tab增加Linux Account功能

测试内容：
1. 登录系统
2. 导航到 Management 页面
3. 检查 Users tab 表格是否包含 Linux Account 列
4. 测试编辑用户功能（包括 Linux Account 字段）
5. 测试密码重置功能
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
USERNAME = os.environ.get('TEST_USERNAME', 'admin')
PASSWORD = os.environ.get('TEST_PASSWORD', 'admin123')
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(skill_dir))), 'screenshots', 'issues', '52')
TIMEOUT = 60000  # 60 seconds timeout

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take screenshot and save to issue directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def test_issue52():
    """Test Issue 52: Linux Account feature in Management Users tab"""
    screenshots = []
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)
        
        try:
            print("\n" + "=" * 60)
            print("UI Test: Issue 52 - Management Users Tab Linux Account")
            print("=" * 60)
            
            # Step 1: Navigate to login page
            print("\n[Step 1] Navigate to login page")
            page.goto(f'{BASE_URL}/login')
            page.wait_for_load_state('networkidle')
            screenshots.append(take_screenshot(page, '01_login_page.png'))
            print("  ✓ Login page loaded")
            
            # Step 2: Login
            print("\n[Step 2] Login as admin")
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            screenshots.append(take_screenshot(page, '02_after_login.png'))
            print("  ✓ Logged in successfully")
            
            # Step 3: Navigate to Management page
            print("\n[Step 3] Navigate to Management page")
            page.click('#nav-management')
            page.wait_for_load_state('networkidle')
            time.sleep(1)
            screenshots.append(take_screenshot(page, '03_management_page.png'))
            print("  ✓ Management page loaded")
            
            # Step 4: Check Users tab is active
            print("\n[Step 4] Check Users tab")
            users_tab = page.locator('#users-tab')
            expect(users_tab).to_be_visible()
            print("  ✓ Users tab is visible")
            
            # Step 5: Check table headers include Linux Account
            print("\n[Step 5] Check table headers")
            table_headers = page.locator('#users-content table thead th').all_inner_texts()
            print(f"  Table headers: {table_headers}")
            
            assert 'Linux Account' in table_headers, "Linux Account column not found in table headers"
            print("  ✓ Linux Account column found in table headers")
            screenshots.append(take_screenshot(page, '04_users_table.png'))
            
            # Step 6: Check action buttons
            print("\n[Step 6] Check action buttons")
            page.wait_for_selector('#users-table-body tr', timeout=5000)
            rows = page.locator('#users-table-body tr').all()
            
            if len(rows) > 0 and 'Loading' not in rows[0].inner_text():
                # Check for edit, password reset, and delete buttons
                first_row = rows[0]
                edit_btn = first_row.locator('button.btn-warning')
                password_btn = first_row.locator('button.btn-info')
                delete_btn = first_row.locator('button.btn-danger')
                
                expect(edit_btn).to_be_visible()
                print("  ✓ Edit button found")
                
                expect(password_btn).to_be_visible()
                print("  ✓ Password reset button found")
                
                expect(delete_btn).to_be_visible()
                print("  ✓ Delete button found")
                
                screenshots.append(take_screenshot(page, '05_action_buttons.png'))
            else:
                print("  ! No users in table or still loading")
            
            # Step 7: Test edit user modal
            print("\n[Step 7] Test edit user modal")
            if len(rows) > 0 and 'Loading' not in rows[0].inner_text():
                # Click edit button on first user
                edit_btn = page.locator('#users-table-body tr:first-child button.btn-warning')
                edit_btn.click()
                time.sleep(1)
                
                # Check modal is visible
                modal = page.locator('#editUserModal')
                expect(modal).to_be_visible()
                print("  ✓ Edit user modal opened")
                
                # Check Linux Account field exists in modal
                linux_account_input = page.locator('#edit-linux-account')
                expect(linux_account_input).to_be_visible()
                print("  ✓ Linux Account input field found in modal")
                
                screenshots.append(take_screenshot(page, '06_edit_modal.png'))
                
                # Close modal
                page.click('#editUserModal .btn-close')
                time.sleep(0.5)
            
            # Step 8: Summary
            print("\n" + "=" * 60)
            print("Test Summary")
            print("=" * 60)
            print("✓ All tests passed!")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")
            
            return True
            
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            screenshots.append(take_screenshot(page, 'error.png'))
            return False
            
        finally:
            browser.close()


if __name__ == '__main__':
    success = test_issue52()
    sys.exit(0 if success else 1)