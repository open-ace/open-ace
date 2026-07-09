"""
E2E test for user role options verification (Issue #1497)

This test verifies that the role options in user management page
include admin, manager, user, and readonly (not viewer).
"""

import os
import sys

from playwright.sync_api import sync_playwright

# Test configuration
BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:5000")
ADMIN_USERNAME = os.environ.get("TEST_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")


def test_user_role_options():
    """Verify role options in user management include correct roles."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Login as admin
            print("Navigating to login page...")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")

            # Fill login form
            page.fill("input[name='username']", ADMIN_USERNAME)
            page.fill("input[name='password']", ADMIN_PASSWORD)
            page.click("button[type='submit']")

            # Wait for redirect
            page.wait_for_url("**/manage**", timeout=10000)
            print("Logged in successfully")

            # Navigate to User Management tab
            page.goto(f"{BASE_URL}/manage")
            page.wait_for_load_state("networkidle")

            # Look for Add User button to trigger modal
            add_user_btn = page.locator("button:has-text('Add User')")
            if add_user_btn.count() > 0:
                add_user_btn.click()
                page.wait_for_selector(".modal", timeout=5000)
                print("Add User modal opened")

                # Find role select dropdown
                role_select = page.locator("select[name='role'], .form-select[name='role']")
                if role_select.count() > 0:
                    # Get all option values
                    options = role_select.locator("option").all()
                    option_values = [opt.get_attribute("value") or opt.text_content() for opt in options]
                    print(f"Role options found: {option_values}")

                    # Verify expected roles
                    expected_roles = ["admin", "manager", "user", "readonly"]
                    missing_roles = []
                    for role in expected_roles:
                        if role in option_values:
                            print(f"✅ Role '{role}' found in options")
                        else:
                            print(f"❌ Role '{role}' NOT found in options")
                            missing_roles.append(role)

                    # Verify 'viewer' is NOT present
                    if "viewer" in option_values:
                        print("❌ 'viewer' role found in options - this should not exist!")
                        return False
                    else:
                        print("✅ 'viewer' role correctly removed from options")

                    if missing_roles:
                        print(f"❌ Test FAILED: Missing roles: {missing_roles}")
                        return False

                    print("✅ Test PASSED: All expected roles present, 'viewer' removed")
                    return True
                else:
                    print("❌ Role select dropdown not found")
                    return False
            else:
                print("❌ Add User button not found")
                return False

        except Exception as e:
            print(f"❌ Test error: {e}")
            return False

        finally:
            browser.close()


if __name__ == "__main__":
    success = test_user_role_options()
    sys.exit(0 if success else 1)
