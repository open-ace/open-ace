#!/usr/bin/env python3
"""
E2E Test for Terminal Session Quota Integration
"""

import os
import time

from playwright.sync_api import sync_playwright

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")


def test_terminal_session():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        # 1. Login
        print("Logging in...")
        page.goto(f"{BASE_URL}/login")
        time.sleep(1)
        page.screenshot(path="/tmp/01_login.png")

        # Fill login form
        page.fill("input[type='text']", "admin")
        page.fill("input[type='password']", "admin123")
        page.click("button[type='submit']")
        time.sleep(2)
        page.wait_for_load_state("networkidle")
        print(f"After login URL: {page.url}")
        page.screenshot(path="/tmp/02_dashboard.png")

        # 2. Navigate to Work page directly
        print("Going to Work page...")
        page.goto(f"{BASE_URL}/work")
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        print(f"Work page URL: {page.url}")
        page.screenshot(path="/tmp/03_work_session_list.png")

        # 3. Check Session List for terminal session
        print("Checking Session List...")
        # Look for sessions with terminal icon
        session_items = page.locator("[class*='session']").all()
        print(f"Found {len(session_items)} session elements")

        # Check for terminal text/icon
        page_content = page.content()
        if "terminal" in page_content.lower() or "Terminal" in page_content:
            print("Found terminal-related content on page!")
        else:
            print("No terminal content found")

        page.screenshot(path="/tmp/04_session_detail.png")

        # 4. Check Status Bar
        print("Checking Status Bar...")
        # Find quota display
        token_display = page.locator("text=/Token.*\\//").all()
        request_display = page.locator("text=/Request.*\\//").all()
        print(f"Token displays: {len(token_display)}, Request displays: {len(request_display)}")

        # Get status bar text
        status_bar = page.locator("[class*='status'], [class*='bar']").first
        if status_bar.is_visible():
            print(f"Status bar text: {status_bar.inner_text()[:100]}")

        page.screenshot(path="/tmp/05_status_bar.png")

        # 5. Navigate to Remote Workspaces
        print("Going to Remote Workspaces...")
        page.goto(f"{BASE_URL}/manage/remote-workspaces")
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        print(f"Remote Workspaces URL: {page.url}")
        page.screenshot(path="/tmp/06_remote_workspaces.png")

        print("Test complete! Screenshots saved to /tmp/")

        browser.close()


if __name__ == "__main__":
    test_terminal_session()
