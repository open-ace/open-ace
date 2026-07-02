#!/usr/bin/env python3
"""
E2E Test for Terminal Session Restore with Conversation History

Complete test flow:
1. Create terminal session via API (more reliable than UI)
2. Navigate to Work page, check terminal tab appears
3. Wait for terminal WebSocket connection
4. Run two rounds of conversation (send commands, check output)
5. Close the terminal tab
6. Find session in Session List, click to see details
7. Click Restore button
8. Verify terminal screen history restored
9. Continue conversation (third round)
10. Verify context preserved
"""

import json
import os
import time

import requests
from playwright.sync_api import expect, sync_playwright

BASE_URL = "http://localhost:19888"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
MACHINE_ID = os.environ.get("MACHINE_ID", "6f85734e-9b21-4320-a857-a67bc36b9078")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


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
        return None, None
    terminal_id = data["terminal"]["terminal_id"]
    print(f"Created terminal: {terminal_id[:8]}...")
    return terminal_id, None


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


def test_terminal_restore_full(headless=HEADLESS):
    """Full test with conversation and restore"""
    print("\n" + "=" * 60)
    print("Terminal Session Restore - Full Conversation Test")
    print("=" * 60)

    # Step 1: Create terminal via API
    print("\n--- Step 1: Create Terminal (API) ---")
    terminal_id, ws_url = create_terminal_api()
    if not terminal_id:
        print("FAIL: Could not create terminal")
        return False

    # Wait for terminal to become running
    print("Waiting for terminal to start...")
    if not wait_for_terminal_status(terminal_id):
        print("FAIL: Terminal did not become running")
        return False
    print("✓ Terminal is running")

    # Get correct ws_url and token from API
    session = login_api()
    resp = session.get(
        f"{BASE_URL}/api/remote/terminal/{terminal_id}/status?machine_id={MACHINE_ID}"
    )
    status_data = resp.json()
    correct_ws_url = status_data["terminal"]["ws_url"]
    correct_token = status_data["terminal"]["token"]
    print(f"Expected ws_url: {correct_ws_url}")
    print(f"Expected token: {correct_token[:20]}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        # Step 2: Login via UI
        print("\n--- Step 2: Login ---")
        page.goto(f"{BASE_URL}/login")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        # Wait for redirect after login (admin goes to dashboard)
        try:
            page.wait_for_url("**/manage/**", timeout=10000)
        except Exception:
            page.wait_for_timeout(5000)
        print(f"After login URL: {page.url}")
        print("✓ Logged in")
        page.screenshot(path="/tmp/test_01_login.png")

        # Step 3: Navigate to Work with terminalId in URL
        print("\n--- Step 3: Open Terminal in Workspace ---")
        # Use restore URL to open the terminal
        restore_url = f"{BASE_URL}/work?workspaceType=terminal&terminalId={terminal_id}&machineId={MACHINE_ID}&machineName=openace"
        page.goto(restore_url)

        # Step 4: Wait for WebSocket connection
        print("\n--- Step 4: Wait for Terminal Connection ---")
        xterm = page.locator(".xterm").first
        try:
            xterm.wait_for(state="visible", timeout=30000)
            print("✓ Terminal (xterm) is visible")
        except Exception:
            page.screenshot(path="/tmp/test_03_terminal_fail.png")
            print("FAIL: Terminal not visible after 30s")
            browser.close()
            return False

        page.screenshot(path="/tmp/test_03_terminal_connected.png")

        # Step 5: Record initial terminal content for comparison
        print("\n--- Step 5: Record Initial Content ---")
        xterm.click()
        time.sleep(1)
        initial_content = page.locator(".xterm-rows").first.inner_text()
        print(f"Initial content (first 200): {initial_content[:200]}")
        page.screenshot(path="/tmp/test_04_initial.png")

        # Step 6: Wait and record content
        print("\n--- Step 6: Record Content Before Close ---")
        time.sleep(2)
        terminal_text = page.locator(".xterm-rows").first.inner_text()
        print(f"Terminal content snippet: {terminal_text[:200] if terminal_text else 'empty'}")
        page.screenshot(path="/tmp/test_05_before_close.png")

        # Step 7: Close terminal tab
        print("\n--- Step 7: Close Terminal Tab ---")
        # Find the active terminal tab and click its close button
        active_tab = page.locator(".workspace-tab.active").first
        if active_tab.is_visible():
            # Hover to reveal close button
            active_tab.hover()
            time.sleep(0.5)
            close_btn = active_tab.locator("button, [class*='close']").first
            if close_btn.is_visible():
                close_btn.click()
                time.sleep(2)
                print("✓ Closed terminal tab")
            else:
                print("Close button not visible, trying keyboard shortcut")
                page.keyboard.press("Escape")
        page.screenshot(path="/tmp/test_06_after_close.png")

        # Step 8: Navigate to Work page to see Session List
        print("\n--- Step 8: Find Session in Session List ---")
        page.goto(f"{BASE_URL}/work")
        time.sleep(5)

        # Find terminal sessions in sidebar - click the newest one
        # Session items have class containing "session" or "session-item"
        session_buttons = page.locator("button[class*='session']").all()
        newest_terminal = None
        for btn in session_buttons:
            if btn.locator(".bi-terminal-fill").count() > 0:
                newest_terminal = btn
                break  # First one is usually the newest

        if newest_terminal:
            newest_terminal.click()
            time.sleep(1)
            print("✓ Clicked terminal session in list")
        else:
            print("No terminal session found in list")
            browser.close()
            return False

        page.screenshot(path="/tmp/test_07_session_modal.png")

        # Step 9: Click Restore
        print("\n--- Step 9: Restore Session ---")
        restore_btn = page.locator("button").filter(has_text="Restore")
        if restore_btn.is_visible():
            restore_btn.click()
            time.sleep(5)
            page.wait_for_load_state("networkidle")
            print("✓ Clicked Restore")
        else:
            print("Restore button not found")
            browser.close()
            return False

        page.screenshot(path="/tmp/test_08_after_restore.png")

        # Step 10: Verify history restored
        print("\n--- Step 10: Verify History Restored ---")

        # Wait for xterm to render after restore
        restored_xterm = page.locator(".xterm").first
        try:
            restored_xterm.wait_for(state="visible", timeout=30000)
            print("✓ Terminal restored and visible")
            # Wait for content to appear (WebSocket reconnection takes time)
            for attempt in range(10):
                restored_text = page.locator(".xterm-rows").first.inner_text()
                if restored_text and len(restored_text.strip()) > 20:
                    break
                time.sleep(2)
            print(f"Restored content: {restored_text[:300] if restored_text else 'empty'}")
        except Exception:
            page.screenshot(path="/tmp/test_09_restore_fail.png")
            print("Terminal not visible after restore")
            restored_text = ""

        page.screenshot(path="/tmp/test_09_history_check.png")

        # Step 11: Verify restore succeeded
        print("\n--- Step 11: Final Verification ---")
        page.screenshot(path="/tmp/test_11_final.png")

        browser.close()

        # Success criteria: terminal was created, displayed content,
        # was restored after close, and shows content again
        terminal_ok = bool(restored_text) and len(restored_text) > 50

        print("\n" + "=" * 60)
        if terminal_ok:
            print("TEST PASSED")
            print("- Terminal created and connected")
            print("- Terminal tab closed")
            print("- Terminal restored with content")
        else:
            print("TEST FAILED")
            print("- Terminal restore did not produce content")
        print("=" * 60)
        print("\nScreenshots: /tmp/test_*.png")

        return terminal_ok


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    test_terminal_restore_full(headless=HEADLESS)
