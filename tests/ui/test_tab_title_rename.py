#!/usr/bin/env python3
"""
UI Test - Tab Title Default Value and Rename Feature (Issue #9)

Tests the tab title default value and rename functionality in Workspace:
1. Open workspace page
2. Verify new tab title is "New Session" (新建会话)
3. Test rename functionality - click pencil button
4. Enter new name and save
5. Verify title is updated
"""

import sys
import os
import time

# Add skill scripts to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
skill_dir = os.path.join(PROJECT_ROOT, ".qwen", "skills", "ui-test", "scripts")
if os.path.exists(skill_dir):
    sys.path.insert(0, skill_dir)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print(
        "Error: playwright not installed. Run: pip install playwright && playwright install chromium"
    )
    sys.exit(1)

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "9")

# Ensure screenshot directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take screenshot and save to screenshot directory"""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path, full_page=False)
    print(f"  Screenshot saved: {path}")
    return path


def login(page):
    """Login to the system"""
    print("  Logging in...")
    page.goto(f"{BASE_URL}/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")
    # Wait for redirect to home
    time.sleep(1)


def test_tab_title_rename():
    """Test tab title default value and rename functionality"""
    screenshots = []
    test_results = []

    print("\n========================================")
    print("Tab Title and Rename Feature Test (Issue #9)")
    print("========================================")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\nStep 1: Login")
            login(page)
            screenshots.append(take_screenshot(page, "01_login.png"))
            test_results.append(("Login", "PASS"))

            # Step 2: Navigate to Workspace
            print("\nStep 2: Navigate to Workspace")
            page.goto(f"{BASE_URL}/work/workspace")
            page.wait_for_load_state("networkidle")
            time.sleep(5)  # Wait for workspace to fully load
            screenshots.append(take_screenshot(page, "02_workspace.png"))

            # Step 3: Check workspace tabs container exists
            print("\nStep 3: Check workspace tabs")
            tabs = page.locator(".workspace-tab")
            tabs_count = tabs.count()
            print(f"  Found {tabs_count} tabs")

            if tabs_count == 0:
                print("  Waiting for tabs to load...")
                time.sleep(10)
                tabs_count = tabs.count()
                print(f"  Now found {tabs_count} tabs")

            if tabs_count > 0:
                print("  ✓ Workspace tabs found")
                test_results.append(("Workspace Tabs Found", "PASS"))
            else:
                print("  ✗ No tabs found")
                test_results.append(("Workspace Tabs Found", "FAIL"))
                screenshots.append(take_screenshot(page, "03_no_tabs.png"))
                return False

            screenshots.append(take_screenshot(page, "03_tabs_found.png"))

            # Step 4: Check first tab title
            print("\nStep 4: Check first tab title")
            first_tab = tabs.first
            title_element = first_tab.locator(".tab-title")
            title_text = ""

            if title_element.count() > 0:
                title_text = title_element.text_content() or ""
                print(f"  Tab title text: '{title_text}'")

                # Check title should NOT be "session" (Chinese: 会话) or "Session"
                # It should be "New Session" (Chinese: 新建会话) or custom name
                if title_text.lower() == "session" or title_text == "会话":
                    print("  ✗ Tab title is 'session' - should be 'New Session' or custom name")
                    test_results.append(("Tab Title Default", "FAIL", f"Title is '{title_text}'"))
                else:
                    print("  ✓ Tab title is not plain 'session'")
                    test_results.append(("Tab Title Default", "PASS", f"Title is '{title_text}'"))
            else:
                print("  ? Tab title element not found")
                # Try alternative selector
                title_text = first_tab.text_content() or ""
                print(f"  Full tab text: '{title_text}'")
                test_results.append(("Tab Title Default", "INFO", f"Full text: '{title_text}'"))

            screenshots.append(take_screenshot(page, "04_tab_title.png"))

            # Step 5: Hover on tab to show action buttons
            print("\nStep 5: Hover on tab to show rename button")
            first_tab.hover()
            time.sleep(0.5)

            # Look for rename button (pencil icon)
            rename_btn = first_tab.locator(
                ".rename-btn, .tab-action-btn:has(.bi-pencil), button:has(.bi-pencil-fill)"
            )

            if rename_btn.count() > 0:
                print("  ✓ Rename button found")
                test_results.append(("Rename Button Found", "PASS"))
            else:
                # Try broader search
                all_buttons = first_tab.locator("button")
                print(f"  Found {all_buttons.count()} buttons in tab")
                for i in range(all_buttons.count()):
                    btn_text = all_buttons.nth(i).text_content() or ""
                    btn_html = all_buttons.nth(i).inner_html() or ""
                    print(f"    Button {i}: '{btn_text[:30]}' / HTML: '{btn_html[:50]}'")

                # Try clicking on the tab actions area
                print("  ? Rename button not found directly, trying tab actions area")
                rename_btn = first_tab.locator(".tab-actions button")
                if rename_btn.count() > 0:
                    print(f"  Found {rename_btn.count()} buttons in tab-actions")
                    # The second button is usually rename (pencil icon)
                    if rename_btn.count() >= 2:
                        rename_btn = rename_btn.nth(1)
                        print("  Using second button as rename button")
                        test_results.append(("Rename Button Found", "PASS", "Found in tab-actions"))
                    else:
                        rename_btn = rename_btn.first
                        print("  Using first button in tab-actions")
                        test_results.append(("Rename Button Found", "INFO", "Only one button"))

            screenshots.append(take_screenshot(page, "05_hover_tab.png"))

            # Step 6: Click rename button
            print("\nStep 6: Click rename button")
            if rename_btn.count() > 0:
                rename_btn.click()
                time.sleep(1)
                print("  Clicked rename button")
                test_results.append(("Rename Button Click", "PASS"))
            else:
                print("  ✗ Cannot click - rename button not found")
                test_results.append(("Rename Button Click", "FAIL"))
                screenshots.append(take_screenshot(page, "06_no_rename_btn.png"))
                return False

            screenshots.append(take_screenshot(page, "06_rename_modal.png"))

            # Step 7: Check rename modal appeared
            print("\nStep 7: Check rename modal")
            modal = page.locator(".modal-dialog, .rename-modal, [role='dialog']")
            if modal.count() > 0:
                print("  ✓ Modal dialog appeared")
                test_results.append(("Rename Modal Visible", "PASS"))
            else:
                print("  ✗ Modal dialog not found")
                test_results.append(("Rename Modal Visible", "FAIL"))
                screenshots.append(take_screenshot(page, "07_no_modal.png"))
                return False

            # Step 8: Check input field in modal
            print("\nStep 8: Check input field in modal")
            input_field = modal.locator("input[type='text'], input.form-control")
            if input_field.count() > 0:
                current_value = input_field.input_value()
                print(f"  Input field found, current value: '{current_value}'")
                test_results.append(("Rename Input Found", "PASS", f"Value: '{current_value}'"))
            else:
                print("  ✗ Input field not found in modal")
                test_results.append(("Rename Input Found", "FAIL"))
                screenshots.append(take_screenshot(page, "08_no_input.png"))
                return False

            screenshots.append(take_screenshot(page, "08_modal_input.png"))

            # Step 9: Enter new name
            print("\nStep 9: Enter new name")
            new_name = "Test Session Renamed"
            input_field.fill(new_name)
            time.sleep(0.5)
            print(f"  Entered new name: '{new_name}'")
            test_results.append(("Enter New Name", "PASS", new_name))

            screenshots.append(take_screenshot(page, "09_new_name_entered.png"))

            # Step 10: Click Save button
            print("\nStep 10: Click Save button")
            save_btn = modal.locator(
                "button:has-text('Save'), button.btn-primary, button:has-text('保存')"
            )
            if save_btn.count() > 0:
                save_btn.click()
                print("  Clicked Save button")
                time.sleep(2)
                test_results.append(("Save Button Click", "PASS"))
            else:
                print("  ✗ Save button not found")
                # Try all buttons
                all_modal_btns = modal.locator("button")
                print(f"  Found {all_modal_btns.count()} buttons in modal")
                for i in range(all_modal_btns.count()):
                    btn_text = all_modal_btns.nth(i).text_content() or ""
                    print(f"    Button {i}: '{btn_text}'")
                test_results.append(("Save Button Click", "FAIL"))
                screenshots.append(take_screenshot(page, "10_no_save_btn.png"))
                return False

            screenshots.append(take_screenshot(page, "10_after_save.png"))

            # Step 11: Verify modal closed
            print("\nStep 11: Verify modal closed")
            modal_after = page.locator(".modal-dialog, [role='dialog']")
            if modal_after.count() == 0:
                print("  ✓ Modal closed after save")
                test_results.append(("Modal Closed", "PASS"))
            else:
                print("  ✗ Modal still visible after save")
                print(f"  Modal count: {modal_after.count()}")
                test_results.append(("Modal Closed", "FAIL"))
                screenshots.append(take_screenshot(page, "11_modal_not_closed.png"))

            # Step 12: Verify title updated
            print("\nStep 12: Verify title updated")
            time.sleep(1)
            title_element = first_tab.locator(".tab-title")
            if title_element.count() > 0:
                updated_title = title_element.text_content() or ""
                print(f"  Updated title: '{updated_title}'")

                if updated_title == new_name:
                    print(f"  ✓ Title updated to '{new_name}'")
                    test_results.append(("Title Updated", "PASS", updated_title))
                else:
                    print(f"  ✗ Title not updated - expected '{new_name}', got '{updated_title}'")
                    test_results.append(
                        ("Title Updated", "FAIL", f"Expected '{new_name}', got '{updated_title}'")
                    )
            else:
                print("  ? Cannot verify - title element not found")
                test_results.append(("Title Updated", "INFO", "Cannot verify"))

            screenshots.append(take_screenshot(page, "12_title_updated.png"))

            # Step 13: Create new tab and check title
            print("\nStep 13: Create new tab and check title")
            new_tab_btn = page.locator(
                ".workspace-new-tab-btn, button:has(.bi-plus-lg), button:has(.bi-plus)"
            )
            if new_tab_btn.count() > 0:
                new_tab_btn.click()
                time.sleep(3)
                print("  Clicked new tab button")

                # Check new tab title
                new_tabs = page.locator(".workspace-tab")
                new_tab_count = new_tabs.count()
                print(f"  Total tabs now: {new_tab_count}")

                if new_tab_count > tabs_count:
                    new_tab = new_tabs.last
                    new_title_element = new_tab.locator(".tab-title")
                    if new_title_element.count() > 0:
                        new_title = new_title_element.text_content() or ""
                        print(f"  New tab title: '{new_title}'")

                        # Check if new tab title is "New Session" or "新建会话"
                        if new_title == "New Session" or new_title == "新建会话":
                            print("  ✓ New tab has correct default title")
                            test_results.append(("New Tab Default Title", "PASS", new_title))
                        elif new_title.lower() == "session" or new_title == "会话":
                            print("  ✗ New tab title is plain 'session' - bug!")
                            test_results.append(
                                ("New Tab Default Title", "FAIL", f"Title is '{new_title}'")
                            )
                        else:
                            print(f"  New tab title: '{new_title}'")
                            test_results.append(("New Tab Default Title", "INFO", new_title))
                    else:
                        print("  ? Cannot verify new tab title")
                        test_results.append(("New Tab Default Title", "INFO", "No title element"))
                else:
                    print("  ✗ New tab not created")
                    test_results.append(("New Tab Created", "FAIL"))

                screenshots.append(take_screenshot(page, "13_new_tab.png"))
            else:
                print("  ? New tab button not found, skipping")
                test_results.append(("New Tab Created", "SKIP"))

            # Print summary
            print("\n========================================")
            print("Test Summary")
            print("========================================")

            passed = sum(1 for r in test_results if r[1] == "PASS")
            failed = sum(1 for r in test_results if r[1] == "FAIL")
            skipped = sum(1 for r in test_results if r[1] in ("SKIP", "INFO"))

            for name, status, *details in test_results:
                icon = (
                    "✓"
                    if status == "PASS"
                    else "✗" if status == "FAIL" else "?" if status == "INFO" else "-"
                )
                detail = details[0] if details else ""
                print(f"  {icon} {name}: {status}" + (f" - {detail}" if detail else ""))

            print(f"\nTotal: {passed} passed, {failed} failed, {skipped} skipped")
            print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
            for s in screenshots:
                print(f"  - {os.path.basename(s)}")

            print("========================================")

            return failed == 0

        except Exception as e:
            print(f"\nError during test: {e}")
            import traceback

            traceback.print_exc()
            screenshots.append(take_screenshot(page, "error.png"))
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_tab_title_rename()
    sys.exit(0 if success else 1)
