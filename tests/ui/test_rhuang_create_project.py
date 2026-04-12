#!/usr/bin/env python3
"""
UI Test: Create Project as rhuang user

Test the Add Project flow with rhuang user (not admin)
"""

import sys
import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://192.168.64.3:5000")
USERNAME = os.environ.get("USERNAME", "rhuang")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "false").lower() == "false"

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")


def test_create_project_as_rhuang():
    print("=" * 60)
    print("Create Project Test - rhuang user")
    print("=" * 60)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.clear_cookies()
        page = context.new_page()

        # Listen for console messages
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        # Track API responses
        api_responses = []
        page.on("response", lambda res: api_responses.append({"url": res.url, "status": res.status, "method": res.request.method}) if "/api/" in res.url else None)

        try:
            # Step 1: Login as rhuang
            print(f"\n[1] Login as {USERNAME}...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            time.sleep(3)
            page.goto(f"{BASE_URL}/work", timeout=30000)
            time.sleep(2)
            print(f"  Logged in, URL: {page.url}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_01_login.png"))

            # Step 2: Wait for iframe
            print("\n[2] Wait for workspace iframe...")
            time.sleep(5)

            iframe_element = page.locator("iframe[src*='token']")
            iframe_count = iframe_element.count()
            print(f"  Found {iframe_count} iframe(s)")

            if iframe_count == 0:
                print("  ERROR: iframe not found")
                browser.close()
                return False

            iframe_src = iframe_element.first.get_attribute("src")
            print(f"  iframe src: {iframe_src[:100]}...")

            iframe = page.frame_locator("iframe").first
            time.sleep(3)

            # Step 3: Click Add Project
            print("\n[3] Click Add Project button...")
            add_btn = iframe.locator("button:has-text('Add Project')")
            if add_btn.count() == 0:
                print("  ERROR: Add Project button not found")
                browser.close()
                return False

            add_btn.first.click()
            time.sleep(2)
            print("  Clicked Add Project")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_02_modal.png"))

            # Step 4: Wait for directory browser
            print("\n[4] Wait for DirectoryBrowser...")
            time.sleep(3)

            browse_calls = [r for r in api_responses if "/api/fs/browse" in r["url"]]
            print(f"  Browse API calls: {len(browse_calls)}")
            for call in browse_calls:
                print(f"    - Status: {call['status']}")

            # Step 5: Navigate to tmp and create new folder
            print("\n[5] Navigate to /tmp...")

            # Try to find tmp folder or navigate up to root
            tmp_btn = iframe.locator("button:has-text('tmp')")
            if tmp_btn.count() > 0:
                tmp_btn.first.click()
                time.sleep(2)
                print("  Found and clicked tmp folder")
            else:
                # Navigate up to root then find tmp
                print("  tmp folder not visible, trying to navigate...")
                # Look at current path in breadcrumb
                breadcrumb = iframe.locator("[class*='breadcrumb'], div:has-text('/')")
                print(f"  Breadcrumb content available")

                # Try clicking home then navigate
                home_icon = iframe.locator("button:has(svg[class*='HomeIcon']), svg[class*='home']")
                if home_icon.count() > 0:
                    print("  Home icon found")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_03_browse.png"))

            # Step 6: Click New Folder to create unique project
            print("\n[6] Click New Folder button...")
            new_folder_btn = iframe.locator("button:has-text('New Folder'), button:has(svg[class*='FolderPlus'])")
            new_folder_count = new_folder_btn.count()
            print(f"  New Folder buttons: {new_folder_count}")

            if new_folder_count > 0:
                new_folder_btn.first.click()
                time.sleep(1)
                print("  Clicked New Folder")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_04_new_folder_input.png"))

                # Enter folder name
                import random
                folder_name = f"test-{random.randint(10000, 99999)}"
                folder_input = iframe.locator("input").last
                folder_input.fill(folder_name)
                time.sleep(0.5)
                print(f"  Entered: {folder_name}")

                # Click Create button in new folder dialog
                create_btn_dialog = iframe.locator("button:has-text('Create')").first
                create_btn_dialog.click()
                time.sleep(2)
                print("  Created folder")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_05_after_new_folder.png"))

            # Step 7: Select the folder
            print("\n[7] Select This Folder...")
            select_btn = iframe.locator("button:has-text('Select This Folder')")
            if select_btn.count() > 0:
                select_btn.first.click()
                time.sleep(3)
                print("  Selected folder")
            else:
                print("  Select This Folder button not found")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_06_selected.png"))

            # Step 8: Check details step
            print("\n[8] Check details step...")
            time.sleep(2)

            check_path_calls = [r for r in api_responses if "/api/fs/check-path" in r["url"]]
            print(f"  Check-path API calls: {len(check_path_calls)}")
            for call in check_path_calls:
                print(f"    - Status: {call['status']}")

            # Step 9: Click Create/Add Project button
            print("\n[9] Click Create/Add Project button...")
            create_btn = iframe.locator("button[type='submit'], button:has-text('Add Project')")
            create_count = create_btn.count()
            print(f"  Create buttons: {create_count}")

            if create_count > 0:
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_07_before_create.png"))
                create_btn.first.click()
                time.sleep(3)
                print("  Clicked Create button")

                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_08_after_create.png"))

            # Step 10: Check API response
            print("\n[10] Check API response...")
            time.sleep(3)

            projects_calls = [r for r in api_responses if "/api/projects" in r["url"]]
            print(f"  Projects API calls: {len(projects_calls)}")
            for call in projects_calls:
                print(f"    - {call['method']} {call['url'][:60]}... -> {call['status']}")

            # Check console errors
            console_errors = [m for m in console_messages if "error" in m.lower()]
            if console_errors:
                print(f"\n  Console errors found:")
                for err in console_errors[:5]:
                    print(f"    - {err}")

            # Check for success/error in UI
            success_text = iframe.locator("text=/Created|Success|完成/")
            error_text = iframe.locator("text=/Failed|Error|失败|already exists/")

            if success_text.count() > 0:
                print("\n  SUCCESS: Project created!")
            elif error_text.count() > 0:
                err_content = error_text.first.text_content()
                print(f"\n  ERROR shown: {err_content}")

            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_09_final.png"))

            print("\n" + "=" * 60)
            print("Test Complete - Check screenshots")
            print("=" * 60)

        except Exception as e:
            print(f"\n[EXCEPTION] {e}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_rhuang_exception.png"))

        finally:
            browser.close()

    return True


if __name__ == "__main__":
    test_create_project_as_rhuang()