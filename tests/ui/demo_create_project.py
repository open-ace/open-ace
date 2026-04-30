#!/usr/bin/env python3
"""
UI Test: Create Project Flow

Demonstrates how to create a project in the Work mode iframe.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = False  # Show browser for demonstration


def test_create_project_flow():
    print("=" * 60)
    print("Create Project Flow Demo")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        try:
            # Login
            print("\n[1] Login to Open-ACE...")
            page.goto(f"{BASE_URL}/login", timeout=30000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click("button[type='submit']")
            time.sleep(3)
            print(f"  Logged in, URL: {page.url}")

            # Should be in Work mode now
            # Look for project selector or add project button
            print("\n[2] Looking for project selector...")
            time.sleep(2)

            # Check if we're in iframe or main page
            frames = page.frames
            print(f"  Frames found: {len(frames)}")

            # Try to find the add project button (+ icon)
            add_btn = page.locator("button:has(svg[class*='plus']), [data-testid='add-project']")
            count = add_btn.count()
            print(f"  Add project button count: {count}")

            if count > 0:
                print("\n[3] Click add project button...")
                add_btn.first.click()
                time.sleep(2)
                print("  Modal should be open now")

                # Check for DirectoryBrowser
                dir_browser = page.locator(".directory-browser, [data-testid='directory-browser']")
                print(f"  Directory browser found: {dir_browser.count() > 0}")

                # Take screenshot
                page.screenshot(path="/tmp/create_project_demo.png")
                print("  Screenshot saved: /tmp/create_project_demo.png")

                input("\nPress Enter to close browser...")
            else:
                print("  Add project button not found - may need to navigate to correct page")
                page.screenshot(path="/tmp/work_page.png")
                print("  Screenshot: /tmp/work_page.png")

        except Exception as e:
            print(f"  Error: {e}")
            page.screenshot(path="/tmp/error.png")

        finally:
            browser.close()


if __name__ == "__main__":
    test_create_project_flow()
