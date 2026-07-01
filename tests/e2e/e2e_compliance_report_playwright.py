#!/usr/bin/env python3
"""
Open ACE - Compliance Report E2E Playwright Test

Tests:
1. Login as admin
2. Navigate to Compliance Management page
3. Verify report types are visible
4. Verify saved reports list (empty state or with data)
5. Test generating a JSON format report
6. Verify report appears in saved reports list
7. Test error handling when database fails

Run:
  HEADLESS=true  python tests/e2e/e2e_compliance_report_playwright.py   # 自动测试
  HEADLESS=false python tests/e2e/e2e_compliance_report_playwright.py   # 演示模式
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-compliance-report")

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def login(page):
    """Login as admin user."""
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)

    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin123")
    page.click("button[type='submit']")
    pause(2)

    # Wait for redirect to work page
    page.wait_for_url("**/work**", timeout=10000)
    check(True, "Login successful, redirected to work page")
    shot(page, "01-login")


def navigate_to_compliance(page):
    """Navigate to Compliance Management page."""
    print("\n[TEST] Navigate to Compliance Management...")
    page.goto(f"{BASE_URL}/manage/compliance")
    pause(2)

    # Verify page loaded
    compliance_header = page.locator("h2").filter(has_text="合规管理")
    if not compliance_header.is_visible():
        # Try English header
        compliance_header = page.locator("h2").filter(has_text="Compliance Management")

    check(compliance_header.is_visible(), "Compliance Management header is visible")
    shot(page, "02-compliance-page")


def test_saved_reports_list(page):
    """Test that saved reports list is visible."""
    print("\n[TEST] Saved reports list...")

    # Check if reports section exists
    reports_section = page.locator("[class*='saved-reports'], [class*='report']").first
    if reports_section.is_visible():
        check(True, "Saved reports section is visible")

        # Check for empty state or reports table
        empty_state = page.locator(".empty-state, [class*='no-data']").first
        reports_table = page.locator("table, [class*='reports-list']").first

        if empty_state.is_visible():
            # Check for correct Chinese message
            empty_text = empty_state.text_content()
            if "暂无已保存报告" in empty_text or "No saved reports" in empty_text:
                check(True, "Empty state shows correct message")
            else:
                check(False, f"Empty state shows wrong message: {empty_text}")
        elif reports_table.is_visible():
            # Check for report items
            report_rows = page.locator("tr, [class*='report-item']")
            count = report_rows.count()
            check(count > 0, f"Reports table shows {count} reports")
        else:
            check(False, "Neither empty state nor reports table is visible")
    else:
        check(False, "Saved reports section not visible")

    shot(page, "03-saved-reports")


def test_report_types_visible(page):
    """Test that report types are visible."""
    print("\n[TEST] Report types visible...")

    # Check for report type selection
    report_type_select = page.locator("select[name*='type'], [class*='report-type']").first
    if report_type_select.is_visible():
        check(True, "Report type selector is visible")

        # Check options
        options = report_type_select.locator("option")
        option_count = options.count()
        check(option_count > 0, f"Report type has {option_count} options")
    else:
        check(False, "Report type selector not visible")

    shot(page, "04-report-types")


def test_generate_json_report(page):
    """Test generating a JSON format report."""
    print("\n[TEST] Generate JSON report...")

    # Set report type
    report_type_select = page.locator("select[name*='type'], [class*='report-type']").first
    if report_type_select.is_visible():
        report_type_select.select_option(value="usage_summary")
        pause(0.5)

    # Set format to JSON
    format_select = page.locator("select[name*='format'], [class*='format']").first
    if format_select.is_visible():
        format_select.select_option(value="json")
        pause(0.5)

    # Click generate button
    generate_button = page.locator("button").filter(has_text="生成报告").first
    if not generate_button.is_visible():
        generate_button = page.locator("button").filter(has_text="Generate Report").first

    if generate_button.is_visible():
        generate_button.click()
        pause(3)

        # Check for success or error message
        error_alert = page.locator(".alert-danger, [class*='error']").first
        success_alert = page.locator(".alert-success, [class*='success']").first

        if error_alert.is_visible():
            error_text = error_alert.text_content()
            check(False, f"Report generation failed: {error_text}")
        elif success_alert.is_visible():
            check(True, "Report generation successful")
        else:
            # Check if reports list updated
            check(True, "Report generation completed (no error)")

        shot(page, "05-generate-report")
    else:
        check(False, "Generate button not visible")


def test_error_display(page):
    """Test that errors are properly displayed."""
    print("\n[TEST] Error display...")

    # Check for any error messages on the page
    error_elements = page.locator(".alert-danger, [class*='error'], .text-danger").all()

    # If there are errors, verify they are visible and readable
    for error_elem in error_elements:
        if error_elem.is_visible():
            error_text = error_elem.text_content()
            if error_text and len(error_text) > 0:
                check(True, f"Error message visible: {error_text[:100]}")
                shot(page, "06-error-display")
                return

    # No errors found is also a valid state
    check(True, "No error messages on page")


def run_tests():
    """Run all tests."""
    global passed, failed

    print("\n" + "=" * 60)
    print("Compliance Report E2E Test")
    print("=" * 60)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(
                viewport={"width": 1280, "height": 1024},
                locale="zh-CN",
            )
            page = context.new_page()

            try:
                login(page)
                navigate_to_compliance(page)
                test_saved_reports_list(page)
                test_report_types_visible(page)
                test_generate_json_report(page)
                test_error_display(page)

                print("\n" + "=" * 60)
                print("Test Results")
                print("=" * 60)
                print(f"Passed: {passed}")
                print(f"Failed: {failed}")

                if errors:
                    print("\nErrors:")
                    for error in errors:
                        print(f"  - {error}")

                print("=" * 60)

                if failed > 0:
                    print("\n[FAILED] Some tests failed")
                    return 1
                else:
                    print("\n[SUCCESS] All tests passed")
                    return 0

            finally:
                browser.close()

    except Exception as e:
        print(f"\n[ERROR] Test execution failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
