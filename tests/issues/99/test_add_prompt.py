#!/usr/bin/env python3
"""
Test script to reproduce the "Add Prompt" dialog error in work mode.

Issue: Clicking the Create button in the Add Prompt dialog causes an error.
"""

import json
import os
import sys
import time
from datetime import datetime

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    print(
        "Error: playwright not installed. Run: pip install playwright && playwright install chromium"
    )
    sys.exit(1)

# Configuration
BASE_URL = "http://localhost:5000"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
TIMEOUT = 30000


def test_add_prompt():
    """Test the Add Prompt dialog functionality."""

    print("=" * 60)
    print("Test: Add Prompt Dialog in Work Mode")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Set to False to see the browser
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size(VIEWPORT_SIZE)

        # Enable console log capture
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))

        # Capture all network requests and responses
        requests_log = []

        def log_request(request):
            if "/api/" in request.url:
                req_info = {
                    "method": request.method,
                    "url": request.url,
                    "post_data": request.post_data,
                }
                requests_log.append(("request", req_info))
                print(f"[Request] {request.method} {request.url}")
                if request.post_data:
                    print(f"  Post Data: {request.post_data[:500]}")

        def log_response(response):
            if "/api/" in response.url:
                resp_info = {"status": response.status, "url": response.url, "body": None}
                try:
                    body = response.text()
                    resp_info["body"] = body[:1000] if body else None
                except:
                    pass
                requests_log.append(("response", resp_info))
                print(f"[Response] {response.status} {response.url}")
                if resp_info["body"]:
                    print(f"  Body: {resp_info['body'][:500]}")

        page.on("request", log_request)
        page.on("response", log_response)

        try:
            # Step 1: Login
            print("\n[Step 1] Logging in...")
            page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/", timeout=10000)
            print("✓ Login successful")

            # Step 2: Navigate to Work mode
            print("\n[Step 2] Navigating to Work mode...")
            page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=TIMEOUT)
            page.wait_for_timeout(2000)
            print(f"Current URL: {page.url}")

            # Step 3: Click on Prompts tab
            print("\n[Step 3] Clicking Prompts tab...")
            prompts_tab = page.locator('button:has-text("Prompts")').first
            prompts_tab.click()
            page.wait_for_timeout(1000)
            print("✓ Clicked Prompts tab")

            # Step 4: Click Add Prompt button
            print("\n[Step 4] Clicking Add Prompt button...")
            add_btn = page.locator('button:has-text("Add Prompt")').first
            add_btn.click()
            page.wait_for_timeout(1000)
            print("✓ Clicked Add Prompt button")

            # Step 5: Fill the form using more specific selectors
            print("\n[Step 5] Filling the form...")

            # Get the modal
            modal = page.locator(".modal.show").first

            # Fill name - look for input with "Name" label nearby
            name_input = modal.locator(
                '.form-group:has(label:has-text("Name")) input, input.form-control'
            ).first
            name_input.fill("Test Prompt")
            print("✓ Filled name: Test Prompt")

            # Fill content - look for textarea
            content_textarea = modal.locator("textarea.form-control").first
            content_textarea.fill("This is a test prompt content with {variable}.")
            print("✓ Filled content")

            # Debug: Print form values
            print("\n[Debug] Checking form values...")
            name_value = name_input.input_value()
            content_value = content_textarea.input_value()
            print(f"  Name input value: '{name_value}'")
            print(f"  Content textarea value: '{content_value}'")

            # Step 6: Click Create button
            print("\n[Step 6] Clicking Create button...")

            # Clear previous network logs
            requests_log.clear()

            # Find the Create button in the modal
            create_btn = modal.locator('button:has-text("Create")').first
            print(f"Create button found: {create_btn.is_visible()}")

            # Click the button
            create_btn.click()
            print("✓ Clicked Create button")

            page.wait_for_timeout(3000)

            # Step 7: Check for errors
            print("\n[Step 7] Checking for errors...")

            # Check console messages
            error_messages = [msg for msg in console_messages if "error" in msg.lower()]
            if error_messages:
                print("\n❌ Console errors found:")
                for msg in error_messages:
                    print(f"  {msg}")

            # Check network responses for errors
            print("\n[Network Requests Log]")
            for log_type, log_info in requests_log:
                if log_type == "response":
                    status = log_info.get("status", 0)
                    if status >= 400:
                        print(f"  ❌ HTTP {status}: {log_info['url']}")
                        if log_info.get("body"):
                            print(f"     Body: {log_info['body']}")
                    else:
                        print(f"  ✓ HTTP {status}: {log_info['url']}")

            # Check for error alert in UI
            try:
                error_alert = modal.locator(".alert-danger, .error").first
                if error_alert.is_visible(timeout=1000):
                    error_text = error_alert.text_content()
                    print(f"\n❌ UI Error: {error_text}")
            except Exception:
                pass

            # Check if modal is still open (error case) or closed (success)
            try:
                modal_still_open = modal.is_visible(timeout=1000)
            except Exception:
                modal_still_open = False

            if modal_still_open:
                print("\n⚠️ Modal is still open - this might indicate an error")
            else:
                print("\n✓ Modal closed - prompt might have been created successfully")

            # Take screenshot
            screenshot_path = (
                "/Users/rhuang/workspace/open-ace/screenshots/issues/99/after_create_click.png"
            )
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            page.screenshot(path=screenshot_path)
            print(f"\nScreenshot saved: {screenshot_path}")

            # Print all console messages for debugging
            print("\n" + "=" * 60)
            print("Console Messages:")
            print("=" * 60)
            for msg in console_messages:
                print(f"  {msg}")

        except Exception as e:
            print(f"\n❌ Test failed with error: {e}")
            import traceback

            traceback.print_exc()

            # Take screenshot on error
            screenshot_path = "/Users/rhuang/workspace/open-ace/screenshots/issues/99/error.png"
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

        finally:
            browser.close()

    print("\n" + "=" * 60)
    print("Test completed")
    print("=" * 60)


if __name__ == "__main__":
    test_add_prompt()
