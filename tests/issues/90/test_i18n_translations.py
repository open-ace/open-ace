"""
Test script for issue #90: Page content should support Chinese/English switching.

This test verifies that all user-visible text on the page supports i18n translation.

Test cases:
1. Login page - all text should be translatable
2. Logout success page - all text should be translatable
3. Main page - all text should be translatable
4. Language switching should update all visible text
"""

import pytest
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from playwright.sync_api import sync_playwright, expect

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")


def take_screenshot(page, name):
    """Take a screenshot and save to screenshots directory."""
    screenshots_dir = project_root / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    # Create subdirectory if path contains /
    if "/" in name:
        parts = name.split("/")
        subdir = screenshots_dir / parts[0]
        subdir.mkdir(exist_ok=True)
        filepath = subdir / (parts[1] if not parts[1].endswith(".png") else parts[1])
    else:
        filepath = screenshots_dir / name

    # Ensure .png extension
    if not str(filepath).endswith(".png"):
        filepath = str(filepath) + ".png"

    page.screenshot(path=str(filepath))
    print(f"  Screenshot saved: {filepath}")
    return str(filepath)


def test_login_page_i18n():
    """Test login page internationalization."""
    print("\n" + "=" * 60)
    print("Test: Login Page i18n")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        results = []

        try:
            # Navigate to login page
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")

            # Take initial screenshot
            take_screenshot(page, "issues/90/01_login_english.png")

            # Check language selector exists
            lang_select = page.locator("#lang-select")
            expect(lang_select).to_be_visible()
            print("  ✓ Language selector is visible")
            results.append(("Language Selector", "PASS", "Visible on login page"))

            # Check English text
            login_subtitle = page.locator("#login-subtitle")
            expect(login_subtitle).to_contain_text("Please sign in to continue")
            print("  ✓ English subtitle is correct")
            results.append(("English Subtitle", "PASS", "Please sign in to continue"))

            username_label = page.locator("#username-label")
            expect(username_label).to_contain_text("Username")
            print("  ✓ English username label is correct")
            results.append(("English Username Label", "PASS", "Username"))

            password_label = page.locator("#password-label")
            expect(password_label).to_contain_text("Password")
            print("  ✓ English password label is correct")
            results.append(("English Password Label", "PASS", "Password"))

            login_btn = page.locator("#login-btn-text")
            expect(login_btn).to_contain_text("Sign In")
            print("  ✓ English login button is correct")
            results.append(("English Login Button", "PASS", "Sign In"))

            # Switch to Chinese
            page.select_option("#lang-select", "zh")
            page.wait_for_timeout(500)

            # Take Chinese screenshot
            take_screenshot(page, "issues/90/02_login_chinese.png")

            # Check Chinese text
            expect(login_subtitle).to_contain_text("请登录以继续")
            print("  ✓ Chinese subtitle is correct")
            results.append(("Chinese Subtitle", "PASS", "请登录以继续"))

            expect(username_label).to_contain_text("用户名")
            print("  ✓ Chinese username label is correct")
            results.append(("Chinese Username Label", "PASS", "用户名"))

            expect(password_label).to_contain_text("密码")
            print("  ✓ Chinese password label is correct")
            results.append(("Chinese Password Label", "PASS", "密码"))

            expect(login_btn).to_contain_text("登录")
            print("  ✓ Chinese login button is correct")
            results.append(("Chinese Login Button", "PASS", "登录"))

        except Exception as e:
            results.append(("Test Error", "FAIL", str(e)))
            print(f"  ✗ Error: {e}")
        finally:
            browser.close()

    return results


def test_main_page_i18n():
    """Test main page internationalization."""
    print("\n" + "=" * 60)
    print("Test: Main Page i18n")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        results = []

        try:
            # Login first
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("#login-btn")

            # Wait for navigation - could redirect to main page or stay on login
            page.wait_for_timeout(3000)  # Wait for redirect

            # Check if we're on the main page or still on login
            current_url = page.url
            if "/login" in current_url:
                # Check for error message
                error_msg = page.locator(".error-message")
                if error_msg.is_visible():
                    raise Exception(f"Login failed: {error_msg.text_content()}")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)  # Extra wait for JS to initialize

            # Take English screenshot
            take_screenshot(page, "issues/90/03_main_english.png")

            # Check sidebar navigation in English
            nav_analysis = page.locator("#nav-analysis-text")
            expect(nav_analysis).to_contain_text("Analysis")
            print("  ✓ English Analysis nav is correct")
            results.append(("English Analysis Nav", "PASS", "Analysis"))

            nav_management = page.locator("#nav-management-text")
            expect(nav_management).to_contain_text("Management")
            print("  ✓ English Management nav is correct")
            results.append(("English Management Nav", "PASS", "Management"))

            # Switch to Chinese
            page.select_option("#lang-select", "zh")
            page.wait_for_timeout(1000)  # Wait for JS to update

            # Take Chinese screenshot
            take_screenshot(page, "issues/90/04_main_chinese.png")

            # Check sidebar navigation in Chinese
            expect(nav_analysis).to_contain_text("分析")
            print("  ✓ Chinese Analysis nav is correct")
            results.append(("Chinese Analysis Nav", "PASS", "分析"))

            expect(nav_management).to_contain_text("管理")
            print("  ✓ Chinese Management nav is correct")
            results.append(("Chinese Management Nav", "PASS", "管理"))

            # Navigate to Analysis page
            page.click("#nav-analysis")
            page.wait_for_timeout(500)

            # Check Analysis page text
            analysis_title = page.locator("#analysis-title")
            expect(analysis_title).to_contain_text("分析")
            print("  ✓ Chinese Analysis title is correct")
            results.append(("Chinese Analysis Title", "PASS", "分析"))

            # Check quick date buttons
            quick_date_7 = page.locator("#quick-date-7")
            expect(quick_date_7).to_contain_text("最近 7 天")
            print("  ✓ Chinese Quick Date 7 is correct")
            results.append(("Chinese Quick Date 7", "PASS", "最近 7 天"))

            # Switch back to English
            page.select_option("#lang-select", "en")
            page.wait_for_timeout(500)

            # Take English Analysis screenshot
            take_screenshot(page, "issues/90/05_analysis_english.png")

            expect(analysis_title).to_contain_text("Analysis")
            print("  ✓ English Analysis title is correct")
            results.append(("English Analysis Title", "PASS", "Analysis"))

            expect(quick_date_7).to_contain_text("Last 7 Days")
            print("  ✓ English Quick Date 7 is correct")
            results.append(("English Quick Date 7", "PASS", "Last 7 Days"))

        except Exception as e:
            results.append(("Test Error", "FAIL", str(e)))
            print(f"  ✗ Error: {e}")
        finally:
            browser.close()

    return results


def test_logout_page_i18n():
    """Test logout success page internationalization."""
    print("\n" + "=" * 60)
    print("Test: Logout Page i18n")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        results = []

        try:
            # Navigate to logout success page directly
            page.goto(f"{BASE_URL}/logout")
            page.wait_for_load_state("networkidle")

            # Take English screenshot
            take_screenshot(page, "issues/90/06_logout_english.png")

            # Check language selector exists
            lang_select = page.locator("#lang-select")
            expect(lang_select).to_be_visible()
            print("  ✓ Language selector is visible on logout page")
            results.append(("Language Selector", "PASS", "Visible on logout page"))

            # Check English text
            logout_title = page.locator("#logout-title")
            expect(logout_title).to_contain_text("Logged Out Successfully")
            print("  ✓ English logout title is correct")
            results.append(("English Logout Title", "PASS", "Logged Out Successfully"))

            logout_message = page.locator("#logout-message")
            expect(logout_message).to_contain_text("You have been logged out")
            print("  ✓ English logout message is correct")
            results.append(("English Logout Message", "PASS", "You have been logged out"))

            go_to_login_btn = page.locator("#go-to-login-btn")
            expect(go_to_login_btn).to_contain_text("Go to Login")
            print("  ✓ English Go to Login button is correct")
            results.append(("English Go to Login", "PASS", "Go to Login"))

            # Switch to Chinese
            page.select_option("#lang-select", "zh")
            page.wait_for_timeout(500)

            # Take Chinese screenshot
            take_screenshot(page, "issues/90/07_logout_chinese.png")

            # Check Chinese text
            expect(logout_title).to_contain_text("已成功退出登录")
            print("  ✓ Chinese logout title is correct")
            results.append(("Chinese Logout Title", "PASS", "已成功退出登录"))

            expect(logout_message).to_contain_text("您已退出")
            print("  ✓ Chinese logout message is correct")
            results.append(("Chinese Logout Message", "PASS", "您已退出"))

            expect(go_to_login_btn).to_contain_text("前往登录")
            print("  ✓ Chinese Go to Login button is correct")
            results.append(("Chinese Go to Login", "PASS", "前往登录"))

        except Exception as e:
            results.append(("Test Error", "FAIL", str(e)))
            print(f"  ✗ Error: {e}")
        finally:
            browser.close()

    return results


def main():
    """Run all i18n tests."""
    print("\n" + "=" * 60)
    print("Issue #90: i18n Translation Tests")
    print("=" * 60)

    # Create screenshots directory
    os.makedirs("screenshots/issues/90", exist_ok=True)

    all_results = []

    # Run tests
    all_results.extend(test_login_page_i18n())
    all_results.extend(test_main_page_i18n())
    all_results.extend(test_logout_page_i18n())

    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for r in all_results if r[1] == "PASS")
    failed = sum(1 for r in all_results if r[1] == "FAIL")

    for name, status, message in all_results:
        symbol = "✓" if status == "PASS" else "✗"
        print(f"  {symbol} {name}: {message}")

    print("\n" + "-" * 60)
    print(f"Total: {len(all_results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
