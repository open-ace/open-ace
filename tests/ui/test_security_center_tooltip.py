#!/usr/bin/env python3
"""
Test Security Center Tooltip - Issue #208
Verify that tooltip help icons are displayed in the filter rules table header
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots", "issues", "208"
)

os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_security_center_tooltip():
    """Test Security Center Tooltip functionality"""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            # Step 1: Login
            print("Step 1: Login...")
            page.goto(f"{BASE_URL}/login")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/", timeout=10000)
            time.sleep(1)
            results.append(("Login", "PASS"))
            print("  ✓ Login successful")

            # Step 2: Navigate to Management page (Security Center)
            print("\nStep 2: Navigate to Security Center page...")
            page.goto(f"{BASE_URL}/manage/security")
            time.sleep(2)  # Wait for page to fully load
            results.append(("Navigate to Security Center", "PASS"))
            print("  ✓ Security Center page loaded")

            # Step 3: Verify Security Center page loaded
            print("\nStep 3: Verify Security Center page...")
            # Wait for the page to render
            page.wait_for_selector("h2", timeout=10000)
            screenshot_path = os.path.join(SCREENSHOT_DIR, "01_security_center_page.png")
            page.screenshot(path=screenshot_path)
            print(f"  Screenshot saved: {screenshot_path}")
            results.append(("Security Center page loaded", "PASS"))
            print("  ✓ Security Center page verified")

            # Step 4: Click on Content Filter tab
            print("\nStep 4: Click on Content Filter tab...")
            content_filter_tab = page.locator("text=Content Filter")
            if content_filter_tab.count() > 0:
                content_filter_tab.first.click()
                time.sleep(1)
                results.append(("Click Content Filter tab", "PASS"))
                print("  ✓ Content Filter tab clicked")
            else:
                results.append(("Click Content Filter tab", "FAIL - Tab not found"))
                print("  ✗ Content Filter tab not found")

            # Step 5: Check for tooltip help icons in table header
            print("\nStep 5: Check for tooltip help icons...")
            screenshot_path = os.path.join(SCREENSHOT_DIR, "01_filter_rules_table.png")
            page.screenshot(path=screenshot_path)
            print(f"  Screenshot saved: {screenshot_path}")

            # Check for question-circle icons in table headers
            help_icons = page.locator("th .bi-question-circle")
            icon_count = help_icons.count()
            print(f"  Found {icon_count} help icons in table headers")

            if icon_count >= 3:
                results.append(("Check help icons in table header", "PASS"))
                print("  ✓ Help icons found in table headers (Pattern, Type, Action)")
            else:
                results.append(
                    ("Check help icons in table header", f"FAIL - Found {icon_count} icons")
                )
                print(f"  ✗ Expected at least 3 help icons, found {icon_count}")

            # Step 6: Hover over Pattern help icon and check tooltip
            print("\nStep 6: Hover over Pattern help icon...")
            pattern_help_icon = page.locator("th .bi-question-circle").first
            if pattern_help_icon.count() > 0:
                pattern_help_icon.hover()
                time.sleep(0.5)  # Wait for tooltip to appear

                # Check if tooltip is visible
                tooltip = page.locator(".tooltip.show, .tooltip-inner")
                if tooltip.count() > 0:
                    tooltip_text = tooltip.first.text_content()
                    print(f"  Tooltip text: {tooltip_text}")
                    screenshot_path = os.path.join(SCREENSHOT_DIR, "02_pattern_tooltip.png")
                    page.screenshot(path=screenshot_path)
                    print(f"  Screenshot saved: {screenshot_path}")

                    if (
                        "keyword" in tooltip_text.lower()
                        or "关键词" in tooltip_text
                        or "match" in tooltip_text.lower()
                    ):
                        results.append(("Pattern tooltip visible", "PASS"))
                        print("  ✓ Pattern tooltip shows help text")
                    else:
                        results.append(("Pattern tooltip visible", "FAIL - Unexpected text"))
                        print("  ✗ Tooltip text doesn't contain expected keywords")
                else:
                    results.append(("Pattern tooltip visible", "FAIL - Tooltip not visible"))
                    print("  ✗ Tooltip not visible after hover")
            else:
                results.append(("Pattern tooltip hover", "FAIL - Icon not found"))
                print("  ✗ Pattern help icon not found")

            # Step 7: Hover over Type help icon and check tooltip
            print("\nStep 7: Hover over Type help icon...")
            type_help_icon = page.locator("th .bi-question-circle").nth(1)
            if type_help_icon.count() > 0:
                type_help_icon.hover()
                time.sleep(0.5)

                tooltip = page.locator(".tooltip.show, .tooltip-inner")
                if tooltip.count() > 0:
                    tooltip_text = tooltip.first.text_content()
                    print(f"  Tooltip text: {tooltip_text}")
                    screenshot_path = os.path.join(SCREENSHOT_DIR, "03_type_tooltip.png")
                    page.screenshot(path=screenshot_path)
                    print(f"  Screenshot saved: {screenshot_path}")

                    if (
                        "keyword" in tooltip_text.lower()
                        or "regex" in tooltip_text.lower()
                        or "pii" in tooltip_text.lower()
                    ):
                        results.append(("Type tooltip visible", "PASS"))
                        print("  ✓ Type tooltip shows help text")
                    else:
                        results.append(("Type tooltip visible", "FAIL - Unexpected text"))
                        print("  ✗ Tooltip text doesn't contain expected keywords")
                else:
                    results.append(("Type tooltip visible", "FAIL - Tooltip not visible"))
                    print("  ✗ Tooltip not visible after hover")
            else:
                results.append(("Type tooltip hover", "FAIL - Icon not found"))
                print("  ✗ Type help icon not found")

            # Step 8: Hover over Action help icon and check tooltip
            print("\nStep 8: Hover over Action help icon...")
            action_help_icon = page.locator("th .bi-question-circle").nth(2)
            if action_help_icon.count() > 0:
                action_help_icon.hover()
                time.sleep(0.5)

                tooltip = page.locator(".tooltip.show, .tooltip-inner")
                if tooltip.count() > 0:
                    tooltip_text = tooltip.first.text_content()
                    print(f"  Tooltip text: {tooltip_text}")
                    screenshot_path = os.path.join(SCREENSHOT_DIR, "04_action_tooltip.png")
                    page.screenshot(path=screenshot_path)
                    print(f"  Screenshot saved: {screenshot_path}")

                    if (
                        "warn" in tooltip_text.lower()
                        or "block" in tooltip_text.lower()
                        or "redact" in tooltip_text.lower()
                    ):
                        results.append(("Action tooltip visible", "PASS"))
                        print("  ✓ Action tooltip shows help text")
                    else:
                        results.append(("Action tooltip visible", "FAIL - Unexpected text"))
                        print("  ✗ Tooltip text doesn't contain expected keywords")
                else:
                    results.append(("Action tooltip visible", "FAIL - Tooltip not visible"))
                    print("  ✗ Tooltip not visible after hover")
            else:
                results.append(("Action tooltip hover", "FAIL - Icon not found"))
                print("  ✗ Action help icon not found")

            # Step 9: Click Add Rule button and check modal help text
            print("\nStep 9: Click Add Rule button...")
            add_rule_btn = page.locator("button:has-text('Add Rule'), button:has-text('添加规则')")
            if add_rule_btn.count() > 0:
                add_rule_btn.first.click()
                time.sleep(1)

                screenshot_path = os.path.join(SCREENSHOT_DIR, "05_add_rule_modal.png")
                page.screenshot(path=screenshot_path)
                print(f"  Screenshot saved: {screenshot_path}")

                # Check for help text under Pattern input
                pattern_help_text = page.locator("small.text-muted")
                if pattern_help_text.count() > 0:
                    first_help_text = pattern_help_text.first.text_content()
                    print(f"  Pattern help text in modal: {first_help_text}")

                    if (
                        "keyword" in first_help_text.lower()
                        or "关键词" in first_help_text
                        or "match" in first_help_text.lower()
                    ):
                        results.append(("Modal Pattern help text visible", "PASS"))
                        print("  ✓ Modal Pattern help text visible")
                    else:
                        results.append(
                            ("Modal Pattern help text visible", "FAIL - Unexpected text")
                        )
                        print("  ✗ Help text doesn't contain expected keywords")
                else:
                    results.append(
                        ("Modal Pattern help text visible", "FAIL - Help text not found")
                    )
                    print("  ✗ Pattern help text not found in modal")

                # Close modal
                close_btn = page.locator("button:has-text('Cancel'), button:has-text('取消')")
                if close_btn.count() > 0:
                    close_btn.first.click()
                    time.sleep(0.5)
            else:
                results.append(("Click Add Rule button", "FAIL - Button not found"))
                print("  ✗ Add Rule button not found")

            # Final screenshot
            screenshot_path = os.path.join(SCREENSHOT_DIR, "06_final_state.png")
            page.screenshot(path=screenshot_path)
            print(f"\nFinal screenshot saved: {screenshot_path}")

        except Exception as e:
            print(f"\nError: {e}")
            results.append(("Test execution", f"FAIL - {str(e)}"))
            screenshot_path = os.path.join(SCREENSHOT_DIR, "error_state.png")
            page.screenshot(path=screenshot_path)

        finally:
            browser.close()

    # Print results summary
    print("\n" + "=" * 50)
    print("UI Test Report - Issue #208: Security Center Tooltip")
    print("=" * 50)

    passed = sum(1 for _, status in results if "PASS" in status)
    failed = sum(1 for _, status in results if "FAIL" in status)

    print(f"\nTotal tests: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    print("\nDetails:")
    for name, status in results:
        symbol = "✓" if "PASS" in status else "✗"
        print(f"  {symbol} {name}: {status}")

    print("\n" + "=" * 50)
    print(f"Screenshots saved in: {SCREENSHOT_DIR}")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = test_security_center_tooltip()
    sys.exit(0 if success else 1)
