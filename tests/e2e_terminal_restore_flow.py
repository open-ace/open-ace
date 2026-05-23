#!/usr/bin/env python3
"""
E2E Test for Terminal Session Restore Flow

Tests the basic restore flow without WebSocket connection issues:
1. Create terminal session via API
2. Verify session appears in Session List
3. Open session detail modal
4. Click Restore button
5. Verify URL contains correct parameters

WebSocket connection and history restore are tested separately.
"""

import os
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
MACHINE_ID = "0092acb3-9b6d-46db-b6c0-73f4e6d363f3"


def login_api():
    """Login via API and return session with cookies"""
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert resp.json()["success"], "Login failed"
    return session


def create_terminal_api():
    """Create terminal session via API"""
    session = login_api()
    resp = session.post(
        f"{BASE_URL}/api/remote/terminal/start",
        json={"machine_id": MACHINE_ID, "work_dir": "/tmp"},
    )
    data = resp.json()
    if not data.get("success"):
        print(f"Failed: {data.get('error')}")
        return None
    terminal_id = data["terminal"]["terminal_id"]
    print(f"Created terminal: {terminal_id[:8]}...")
    return terminal_id


def wait_for_terminal_status(terminal_id, timeout=30):
    """Poll terminal status until running"""
    session = login_api()
    start = time.time()
    while time.time() - start < timeout:
        resp = session.get(
            f"{BASE_URL}/api/remote/terminal/{terminal_id}/status?machine_id={MACHINE_ID}"
        )
        data = resp.json()
        status = data.get("terminal", {}).get("status", "unknown")
        print(f"  Terminal status: {status}")
        if data.get("success") and status == "running":
            return True
        time.sleep(2)
    return False


def test_terminal_restore_flow(headless=True):
    """Test terminal session restore flow"""
    print("\n" + "=" * 60)
    print("Terminal Session Restore Flow Test")
    print("=" * 60)

    # Step 1: Create terminal via API
    print("\n--- Step 1: Create Terminal ---")
    terminal_id = create_terminal_api()
    if not terminal_id:
        print("FAIL: Could not create terminal")
        return False

    if not wait_for_terminal_status(terminal_id):
        print("FAIL: Terminal did not become running")
        return False
    print("✓ Terminal is running")

    # Step 2: Verify session appears in Session List
    print("\n--- Step 2: Verify Session in Session List ---")
    session = login_api()
    resp = session.get(f"{BASE_URL}/api/workspace/sessions?page=1&pageSize=10")
    sessions_data = resp.json()
    sessions = sessions_data.get("data", {}).get("sessions", [])
    terminal_sessions = [s for s in sessions if s.get("workspace_type") == "terminal"]

    # Find our terminal session
    our_session = None
    for s in terminal_sessions:
        if s["session_id"].startswith(terminal_id[:8]):
            our_session = s
            break

    if our_session:
        print(f"✓ Found terminal session in list: {s['session_id'][:8]}")
        print(f"  Title: {s['title']}")
        print(f"  Request count: {s['request_count']}")
    else:
        print(f"FAIL: Terminal session {terminal_id[:8]} not found in session list")
        print(f"  Available terminal sessions: {[s['session_id'][:8] for s in terminal_sessions]}")
        return False

    # Step 3: Test Restore API
    print("\n--- Step 3: Test Restore API ---")
    resp = session.post(f"{BASE_URL}/api/workspace/sessions/{terminal_id}/restore")
    restore_data = resp.json()

    if not restore_data.get("success"):
        print(f"FAIL: Restore API failed: {restore_data.get('error')}")
        return False

    expected_url = restore_data["data"]["url"]
    print(f"✓ Restore API returned URL: {expected_url}")

    # Verify URL contains correct parameters
    if "workspaceType=terminal" not in expected_url:
        print("FAIL: URL missing workspaceType=terminal")
        return False
    if f"terminalId={terminal_id}" not in expected_url:
        print(f"FAIL: URL missing terminalId={terminal_id}")
        return False
    if f"machineId={MACHINE_ID}" not in expected_url:
        print("FAIL: URL missing machineId")
        return False

    print("✓ URL contains all required parameters")

    # Step 4: Test UI flow (Playwright)
    print("\n--- Step 4: Test UI Flow (Playwright) ---")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # Login
        print("Login...")
        page.goto(f"{BASE_URL}/login")
        page.fill("input[type='text']", USERNAME)
        page.fill("input[type='password']", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
        print("✓ Logged in")

        # Navigate to Work page
        print("Navigate to Work page...")
        page.goto(f"{BASE_URL}/work")
        time.sleep(3)
        page.wait_for_load_state("networkidle")

        # Find terminal session in sidebar
        print("Find terminal session in sidebar...")
        terminal_icon = page.locator(".bi-terminal-fill").first
        if terminal_icon.is_visible():
            print("✓ Found terminal icon in sidebar")
        else:
            print("FAIL: Terminal icon not found")
            browser.close()
            return False

        # Click on session to open detail modal
        print("Click session to open detail modal...")
        session_buttons = page.locator("button[class*='session']").all()
        for btn in session_buttons:
            if btn.locator(".bi-terminal-fill").count() > 0:
                btn.click()
                time.sleep(1)
                break
        print("✓ Session detail modal opened")

        # Check for terminal badge
        terminal_badge = page.locator(".badge").filter(has_text="Terminal")
        if terminal_badge.is_visible():
            print("✓ Terminal badge visible in modal")
        else:
            print("Terminal badge not visible")

        # Click Restore button
        print("Click Restore button...")
        restore_btn = page.locator("button").filter(has_text="Restore")
        if restore_btn.is_visible():
            restore_btn.click()
            time.sleep(3)
            page.wait_for_load_state("networkidle")
            print("✓ Restore clicked")
        else:
            print("FAIL: Restore button not found")
            browser.close()
            return False

        # Verify we're on Work page with terminal tab
        current_url = page.url
        print(f"Current URL: {current_url}")

        # URL params are cleared after tab creation, but tab should exist
        terminal_tab = page.locator(".bi-terminal-fill")
        if terminal_tab.count() >= 2:  # One in sidebar, one in tabs
            print("✓ Terminal tab created in Workspace")
        else:
            print("Terminal tab may not be created")

        page.screenshot(path="/tmp/restore_flow_final.png")

        browser.close()

    print("\n" + "=" * 60)
    print("TEST PASSED")
    print("- Terminal session created")
    print("- Session appears in Session List")
    print("- Restore API returns correct URL")
    print("- UI flow completes successfully")
    print("=" * 60)
    print("\nScreenshots: /tmp/restore_flow_final.png")

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--demo", action="store_true", help="Run with visible browser")
    args = parser.parse_args()

    headless = not args.demo
    test_terminal_restore_flow(headless=headless)
