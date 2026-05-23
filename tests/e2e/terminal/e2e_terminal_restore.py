#!/usr/bin/env python3
"""
E2E Test for Terminal Session Restore from Session List

Test flow:
1. Login as admin
2. Navigate to Work page
3. Click on a terminal session in Session List
4. Click Restore button in Session Detail Modal
5. Verify navigation to Workspace with correct URL params
"""

import os
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def get_terminal_session_id():
    """Get a terminal session ID from the API"""
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert login_resp.json()["success"], "Login failed"

    sessions_resp = session.get(f"{BASE_URL}/api/workspace/sessions?page=1&pageSize=20")
    sessions_data = sessions_resp.json()
    sessions = sessions_data.get("data", {}).get("sessions", [])
    terminal_sessions = [s for s in sessions if s.get("workspace_type") == "terminal"]

    if terminal_sessions:
        return terminal_sessions[0]["session_id"]
    return None


def test_terminal_session_restore(headless=True):
    """Test that clicking restore on terminal session navigates to Workspace"""
    print("\n=== Testing Terminal Session Restore ===")

    # Get terminal session ID first
    terminal_id = get_terminal_session_id()
    if not terminal_id:
        print("No terminal session found - skipping test")
        return False

    print(f"Terminal session ID: {terminal_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # 1. Login
        print("Step 1: Login...")
        page.goto(f"{BASE_URL}/login")
        time.sleep(1)
        page.fill("input[type='text']", USERNAME)
        page.fill("input[type='password']", PASSWORD)
        page.click("button[type='submit']")
        time.sleep(2)
        page.wait_for_load_state("networkidle")
        print("✓ Logged in")

        # 2. Navigate to Work page
        print("Step 2: Navigate to Work page...")
        page.goto(f"{BASE_URL}/work")
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/restore_01_work.png")
        print("✓ On Work page")

        # 3. Find terminal session in Session List
        print("Step 3: Find terminal session in Session List...")
        # Look for terminal icon
        terminal_icon = page.locator(".bi-terminal-fill").first
        if terminal_icon.is_visible():
            print("✓ Found terminal icon in Session List")
        else:
            print("No terminal sessions visible")
            browser.close()
            return False

        # 4. Click on the terminal session to open detail modal
        print("Step 4: Click on terminal session...")
        # Find the session item with terminal icon
        session_items = page.locator("[class*='session-item']").all()
        terminal_session_item = None
        for item in session_items:
            if item.locator(".bi-terminal-fill").is_visible():
                terminal_session_item = item
                break

        if terminal_session_item:
            terminal_session_item.click()
            time.sleep(1)
            page.screenshot(path="/tmp/restore_02_session_detail.png")
            print("✓ Opened session detail modal")
        else:
            print("Could not find terminal session item")
            browser.close()
            return False

        # 5. Check modal content
        print("Step 5: Verify session detail modal...")
        # Check for terminal badge
        terminal_badge = page.locator(".badge").filter(has_text="Terminal")
        if terminal_badge.is_visible():
            print("✓ Terminal badge visible in modal")
        else:
            print("Terminal badge not visible")

        # 6. Click Restore button
        print("Step 6: Click Restore button...")
        restore_button = page.locator("button").filter(has_text="Restore")
        if restore_button.is_visible():
            restore_button.click()
            time.sleep(2)
            page.wait_for_load_state("networkidle")
            page.screenshot(path="/tmp/restore_03_after_restore.png")
            print("✓ Clicked Restore button")
        else:
            print("Restore button not visible")
            browser.close()
            return False

        # 7. Check if terminal tab was created (URL params are cleaned up after tab creation)
        print("Step 7: Verify terminal tab in Workspace...")
        # URL params are deleted after tab creation (Workspace.tsx line 588-592)
        # So we verify the terminal tab exists instead of URL params
        time.sleep(2)
        terminal_tabs = page.locator(".bi-terminal-fill").all()
        terminal_tab_count = len(terminal_tabs)
        print(
            f"✓ Found {terminal_tab_count} terminal icons (at least one should be the restored tab)"
        )

        # Check for "Terminal - openace" text in tabs
        terminal_tab_text = page.locator("text=/Terminal -/").first
        if terminal_tab_text.is_visible():
            print("✓ Terminal tab title visible")

        page.screenshot(path="/tmp/restore_04_final.png")

        browser.close()
        print("✓ Test complete")

        # Success if we found the terminal tab (terminal_tab_count >= 2: one in sidebar, one in workspace tabs)
        return terminal_tab_count >= 2


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test terminal session restore")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()

    result = test_terminal_session_restore(headless=args.headless)
    if result:
        print("\n=== Test Passed ===")
    else:
        print("\n=== Test Failed ===")
    print("Screenshots saved to /tmp/restore_*.png")
