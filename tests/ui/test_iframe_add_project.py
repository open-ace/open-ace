#!/usr/bin/env python3
"""
UI Test: iframe Add Project - Browse Directory

Tests that the Add Project dialog in the Work mode iframe
can successfully browse directories via the Open-ACE API.

Issue: Failed to browse directory: Not Found
Fix: Use document.referrer as fallback when cross-origin iframe
     cannot access window.parent.location.origin
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright
import time

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = True  # Use headless mode for automated testing

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")


def test_iframe_add_project_browse():
    print("=" * 60)
    print("iframe Add Project - Browse Directory Test")
    print("=" * 60)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    test_passed = False
    error_message = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        # Create context with no cache to ensure fresh JavaScript is loaded
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        # Clear all browser data including cache
        context.clear_cookies()
        # Clear cache by using a new incognito context
        # This ensures fresh JavaScript is loaded
        page = context.new_page()

        # Listen for console messages to detect errors
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        # Listen for network requests to track API calls
        api_requests = []
        page.on("request", lambda req: api_requests.append(req.url) if "/api/fs/browse" in req.url else None)

        # Listen for network responses
        api_responses = []
        page.on("response", lambda res: api_responses.append({"url": res.url, "status": res.status}) if "/api/fs/browse" in res.url else None)

        try:
            # Step 1: Login
            print("\n[1] Login to Open-ACE...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            time.sleep(3)  # Wait for redirect
            # Navigate to work page explicitly
            page.goto(f"{BASE_URL}/work", timeout=30000)
            time.sleep(2)
            print(f"  Logged in, URL: {page.url}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_01_login.png"))

            # Step 2: Wait for workspace iframe to load
            print("\n[2] Wait for workspace iframe...")
            time.sleep(5)  # Wait for iframe to be created

            # Find the iframe
            iframe_element = page.locator("iframe[src*='token']")
            iframe_count = iframe_element.count()
            print(f"  Found {iframe_count} iframe(s) with token")

            if iframe_count == 0:
                # Try alternative iframe selector
                iframe_element = page.locator("iframe")
                iframe_count = iframe_element.count()
                print(f"  Found {iframe_count} iframe(s) total")

            if iframe_count > 0:
                # Get the iframe frame - use the frame locator properly
                iframe = page.frame_locator("iframe").first
                print(f"  Got iframe frame locator")

                # Get iframe src attribute to check if openace_url is included
                iframe_src = iframe_element.first.get_attribute("src")
                print(f"  iframe src: {iframe_src}")

                # Wait for iframe content to load
                time.sleep(3)

                # Check sessionStorage in iframe for openace_url
                try:
                    # Navigate to the iframe URL directly to check sessionStorage
                    iframe_page = context.new_page()

                    # Listen for console messages to see if initTokenFromUrl is called
                    iframe_console = []
                    iframe_page.on("console", lambda msg: iframe_console.append(f"[{msg.type}] {msg.text}"))

                    iframe_page.goto(iframe_src, timeout=30000)
                    time.sleep(2)

                    # Check window.location.search to see URL parameters
                    location_search = iframe_page.evaluate("() => window.location.search")
                    print(f"  window.location.search: {location_search}")

                    # Check if openace_url can be parsed correctly
                    openace_url_param = iframe_page.evaluate("() => new URLSearchParams(window.location.search).get('openace_url')")
                    print(f"  openace_url param value: {openace_url_param}")

                    # Check sessionStorage for openace_url
                    openace_url_stored = iframe_page.evaluate("() => sessionStorage.getItem('qwen-webui-openace-url')")
                    token_stored = iframe_page.evaluate("() => sessionStorage.getItem('qwen-webui-token')")
                    print(f"  sessionStorage openace_url: {openace_url_stored}")
                    print(f"  sessionStorage token: {token_stored}")

                    # Check for console log messages
                    token_logs = [l for l in iframe_console if "Token" in l or "openace" in l.lower()]
                    print(f"  Token-related console logs: {token_logs}")

                    iframe_page.close()
                except Exception as e:
                    print(f"  Failed to check sessionStorage: {e}")

                # Step 3: Find Add Project button inside iframe
                print("\n[3] Find Add Project button in iframe...")
                time.sleep(5)  # Wait for iframe content to fully load

                # Look for various button selectors
                add_btn_selectors = [
                    "button:has-text('Add Project')",
                    "button:has-text('Add')",
                    "button[title='Add Project']",
                    "button[data-testid='add-project']",
                    "button:has(svg[class*='plus'])",
                    "[data-testid='add-project-button']",
                    "button:has(svg)",
                ]

                add_btn = None
                for selector in add_btn_selectors:
                    try:
                        locator = iframe.locator(selector)
                        count = locator.count()
                        print(f"  Selector '{selector}': {count} elements")
                        if count > 0:
                            add_btn = locator.first
                            print(f"  Found Add Project button with selector: {selector}")
                            break
                    except Exception as e:
                        print(f"  Selector '{selector}' error: {e}")
                        continue

                if add_btn:
                    # Step 4: Click Add Project button
                    print("\n[4] Click Add Project button...")
                    add_btn.click()
                    time.sleep(2)
                    
                    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_02_modal_open.png"))

                    # Step 5: Check if DirectoryBrowser loaded successfully
                    print("\n[5] Check DirectoryBrowser status...")
                    time.sleep(2)  # Wait for API call to complete

                    # Check for error messages
                    error_locator = iframe.locator("text=/Failed to browse directory|Error|Not Found/i")
                    error_count = error_locator.count()
                    
                    # Check for directory list (success indicator)
                    dir_list_locator = iframe.locator("button:has-text('Select'), [data-testid='directory-item'], .directory-browser")
                    dir_list_count = dir_list_locator.count()

                    # Check console messages for errors
                    console_errors = [m for m in console_messages if "error" in m.lower() or "failed" in m.lower()]
                    
                    # Check API responses
                    browse_api_calls = [r for r in api_responses if "/api/fs/browse" in r["url"]]
                    
                    print(f"  API calls to /api/fs/browse: {len(browse_api_calls)}")
                    for call in browse_api_calls:
                        print(f"    - {call['url']} -> Status: {call['status']}")

                    print(f"  Error elements found: {error_count}")
                    print(f"  Directory elements found: {dir_list_count}")
                    print(f"  Console errors: {len(console_errors)}")
                    
                    for err in console_errors[:5]:  # Show first 5 errors
                        print(f"    - {err}")

                    # Determine test result
                    if browse_api_calls and all(c["status"] == 200 for c in browse_api_calls):
                        print("\n[RESULT] API call successful - browse directory works!")
                        test_passed = True
                    elif error_count > 0:
                        error_message = "Error message displayed in UI"
                        print(f"\n[RESULT] FAILED - {error_message}")
                    elif console_errors:
                        error_message = console_errors[0] if console_errors else "Unknown console error"
                        print(f"\n[RESULT] FAILED - Console error detected")
                    else:
                        print("\n[RESULT] Checking directory list...")
                        if dir_list_count > 0:
                            test_passed = True
                            print("  Directory list loaded successfully!")
                        else:
                            error_message = "Directory list not loaded (timeout or other issue)"
                            print(f"  {error_message}")

                    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_03_result.png"))

                else:
                    error_message = "Add Project button not found in iframe"
                    print(f"\n[RESULT] FAILED - {error_message}")
                    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_02_no_button.png"))

            else:
                error_message = "iframe not found on work page"
                print(f"\n[RESULT] FAILED - {error_message}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_02_no_iframe.png"))

        except Exception as e:
            error_message = str(e)
            print(f"\n[EXCEPTION] {e}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "test_iframe_error.png"))

        finally:
            browser.close()

    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Status: {'PASSED' if test_passed else 'FAILED'}")
    if error_message:
        print(f"Error: {error_message}")
    print(f"Screenshots saved to: {SCREENSHOT_DIR}")
    print("=" * 60)

    return test_passed


if __name__ == "__main__":
    passed = test_iframe_add_project_browse()
    sys.exit(0 if passed else 1)