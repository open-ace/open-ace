#!/usr/bin/env python3
"""
UI Test: rhuang user - Create Project button test

Tests that rhuang user can create project in workspace.
Issue: Click Create button has no response.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://117.72.38.96:5000")
USERNAME = os.environ.get("USERNAME", "rhuang")
PASSWORD = os.environ.get("PASSWORD", "rhuang123")
HEADLESS = True

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")


def test_rhuang_create_project():
    print("=" * 70)
    print("rhuang user - Create Project Button Test")
    print("=" * 70)
    print(f"BASE_URL: {BASE_URL}")
    print(f"USERNAME: {USERNAME}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 70)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    test_passed = False
    error_message = None
    console_errors = []
    api_calls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Capture console messages
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Capture API requests
        page.on("request", lambda req: api_calls.append({"url": req.url, "method": req.method}) if "api" in req.url else None)

        try:
            # Step 1: Login
            print("\n[1] Login to Open-ACE as rhuang...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            time.sleep(3)
            
            current_url = page.url
            print(f"  After login URL: {current_url}")
            
            if "login" in current_url:
                error_message = "Login failed - still on login page"
                print(f"  ERROR: {error_message}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_01_login_failed.png"))
                return False
            
            print("  Login successful!")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_01_login.png"))

            # Step 2: Navigate to workspace
            print("\n[2] Navigate to workspace page...")
            page.goto(f"{BASE_URL}/work", timeout=30000)
            time.sleep(5)  # Wait for webui to start
            
            current_url = page.url
            print(f"  Current URL: {current_url}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_02_workspace.png"))

            # Step 3: Wait for iframe
            print("\n[3] Wait for iframe to load...")
            
            iframe_found = False
            iframe_src = None
            for attempt in range(10):
                iframe_locator = page.locator("iframe")
                iframe_count = iframe_locator.count()
                print(f"  Attempt {attempt + 1}: Found {iframe_count} iframe(s)")
                
                if iframe_count > 0:
                    iframe_found = True
                    iframe_src = iframe_locator.first.get_attribute("src")
                    print(f"  iframe src: {iframe_src}")
                    break
                
                time.sleep(2)
            
            if not iframe_found:
                error_message = "iframe not found after multiple attempts"
                print(f"  ERROR: {error_message}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_03_no_iframe.png"))
                return False
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_03_iframe_found.png"))

            # Step 4: Get frame locator
            print("\n[4] Get frame locator and check content...")
            frame = page.frame_locator("iframe").first
            
            # Wait for iframe content to load
            time.sleep(5)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_04_iframe_content.png"))

            # Step 5: Find Add Project button
            print("\n[5] Find Add Project button in iframe...")
            
            add_btn_selectors = [
                "button:has-text('Add')",
                "button:has-text('添加')",
                "button:has-text('New')",
                "button:has-text('新建')",
            ]
            
            add_btn = None
            add_btn_selector = None
            for selector in add_btn_selectors:
                try:
                    locator = frame.locator(selector)
                    count = locator.count()
                    if count > 0:
                        add_btn = locator.first
                        add_btn_selector = selector
                        print(f"  Found button with selector: {selector} ({count} elements)")
                        break
                except Exception as e:
                    print(f"  Selector '{selector}' error: {e}")
            
            if not add_btn:
                error_message = "Add Project button not found"
                print(f"  ERROR: {error_message}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_05_no_add_btn.png"))
                return False
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_05_add_btn_found.png"))

            # Step 6: Click Add Project button
            print("\n[6] Click Add Project button...")
            add_btn.click()
            time.sleep(3)  # Wait for modal animation
            print("  Modal should be open")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_06_modal_open.png"))

            # Step 7: Check DirectoryBrowser
            print("\n[7] Check DirectoryBrowser status...")
            time.sleep(3)
            
            # Check for browse API calls
            browse_calls = [c for c in api_calls if "/api/fs/browse" in c["url"]]
            print(f"  Browse API calls: {len(browse_calls)}")
            for call in browse_calls:
                print(f"    - {call['method']} {call['url']}")
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_07_directory_browser.png"))

            # Step 8: Select current folder
            print("\n[8] Select current folder...")
            
            select_btn_selectors = [
                "button:has-text('Select This Folder')",
                "button:has-text('选择此目录')",
                "button:has-text('Select')",
                "button:has-text('选择')",
            ]
            
            select_btn = None
            for selector in select_btn_selectors:
                try:
                    locator = frame.locator(selector)
                    if locator.count() > 0:
                        select_btn = locator.first
                        print(f"  Found select button: {selector}")
                        break
                except Exception as e:
                    print(f"  Selector '{selector}' error: {e}")
            
            if select_btn:
                select_btn.click()
                time.sleep(2)
                print("  Clicked Select This Folder")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_08_after_select.png"))
            else:
                print("  Select button not found, checking current state...")

            # Step 9: Check if in details step
            print("\n[9] Check if in details step (form visible)...")
            time.sleep(2)
            
            name_input = frame.locator("input[type='text']").first
            try:
                if name_input.is_visible(timeout=3000):
                    print("  In details step, form visible")
                    name_input.fill("test-project")
                    time.sleep(1)
                    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_09_details_form.png"))
                else:
                    print("  Not in details step yet")
                    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_09_not_details.png"))
            except Exception as e:
                print(f"  Form check error: {e}")

            # Step 10: Find and click Create button
            print("\n[10] Find and click Create button...")
            
            create_btn_selectors = [
                "button:has-text('Create')",
                "button:has-text('创建')",
                "button:has-text('Add Project')",
                "button:has-text('添加项目')",
                "button[type='submit']",
            ]
            
            create_btn = None
            for selector in create_btn_selectors:
                try:
                    locator = frame.locator(selector)
                    count = locator.count()
                    if count > 0:
                        first_btn = locator.first
                        if first_btn.is_visible():
                            create_btn = first_btn
                            print(f"  Found Create button: {selector}")
                            break
                except Exception as e:
                    print(f"  Selector '{selector}' error: {e}")
            
            if not create_btn:
                error_message = "Create button not found"
                print(f"  ERROR: {error_message}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_10_no_create_btn.png"))
                return False
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_10_create_btn_found.png"))

            # Step 11: Click Create button
            print("\n[11] Click Create button...")
            create_btn.click()
            print("  Clicked Create button!")
            time.sleep(5)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_11_after_create.png"))

            # Step 12: Check API call
            print("\n[12] Check API call...")
            
            project_api_calls = [c for c in api_calls if "/api/projects" in c["url"] and c["method"] == "POST"]
            if project_api_calls:
                print(f"  API call detected: {len(project_api_calls)}")
                for call in project_api_calls:
                    print(f"    - {call['method']} {call['url']}")
                test_passed = True
                print("  SUCCESS: API call made!")
            else:
                print("  No POST /api/projects call detected!")
                error_message = "Create button click did not trigger API call"
                
                modal_backdrop = frame.locator("div.fixed.inset-0")
                if modal_backdrop.count() > 0:
                    print("  Modal still visible - click may have been blocked")
                    error_message = "Create button click blocked, no API call"
            
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_12_final.png"))

        except Exception as e:
            error_message = str(e)
            print(f"\n[EXCEPTION] {e}")
            import traceback
            traceback.print_exc()
            try:
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "rhuang_exception.png"))
            except:
                pass

        finally:
            browser.close()

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    print(f"Status: {'PASSED' if test_passed else 'FAILED'}")
    if error_message:
        print(f"Error: {error_message}")
    
    if console_errors:
        print(f"\nConsole Errors ({len(console_errors)}):")
        for err in console_errors[:10]:
            print(f"  - {err[:200]}")
    
    print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
    print("=" * 70)

    return test_passed


if __name__ == "__main__":
    passed = test_rhuang_create_project()
    sys.exit(0 if passed else 1)