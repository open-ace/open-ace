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

BASE_URL = "http://localhost:5001"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
MACHINE_ID = "0092acb3-9b6d-46db-b6c0-73f4e6d363f3"
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
        page.fill("input[type='text']", USERNAME)
        page.fill("input[type='password']", PASSWORD)
        page.click("button[type='submit']")
        # Wait for redirect after login (admin goes to dashboard)
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        print(f"After login URL: {page.url}")
        print("✓ Logged in")
        page.screenshot(path="/tmp/test_01_login.png")

        # Step 3: Navigate to Work with terminalId in URL
        print("\n--- Step 3: Open Terminal in Workspace ---")
        # Use restore URL to open the terminal
        restore_url = f"{BASE_URL}/work?workspaceType=terminal&terminalId={terminal_id}&machineId={MACHINE_ID}&machineName=openace"
        page.goto(restore_url)
        time.sleep(5)
        page.wait_for_load_state("networkidle")
        print(f"URL: {page.url}")
        page.screenshot(path="/tmp/test_02_terminal_open.png")

        # Step 4: Wait for WebSocket connection
        print("\n--- Step 4: Wait for Terminal Connection ---")
        time.sleep(10)  # Wait for WebSocket connection and terminal render

        # Look for xterm terminal (the actual terminal element)
        xterm = page.locator(".xterm").first
        if xterm.is_visible():
            print("✓ Terminal (xterm) is visible")
        else:
            print("Terminal not visible, waiting more...")
            time.sleep(10)

        page.screenshot(path="/tmp/test_03_terminal_connected.png")

        # Step 5: First command
        print("\n--- Step 5: First Command ---")
        # Click on terminal to focus
        xterm.click()
        time.sleep(1)
        # Type command
        page.keyboard.type("echo 'ROUND1: Hello Terminal'")
        page.keyboard.press("Enter")
        time.sleep(3)  # Wait for output
        print("✓ Sent first command")
        page.screenshot(path="/tmp/test_04_round1.png")

        # Step 6: Second command
        print("\n--- Step 6: Second Command ---")
        page.keyboard.type("echo 'ROUND2: Conversation Test'")
        page.keyboard.press("Enter")
        time.sleep(3)
        print("✓ Sent second command")
        page.screenshot(path="/tmp/test_05_round2.png")

        # Get terminal text content
        terminal_text = page.locator(".xterm-rows").first.inner_text()
        print(f"Terminal content snippet: {terminal_text[:200] if terminal_text else 'empty'}")

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
        time.sleep(3)
        page.wait_for_load_state("networkidle")

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
        time.sleep(10)  # Wait for WebSocket reconnection and screen restore

        # Check terminal content
        restored_xterm = page.locator(".xterm").first
        if restored_xterm.is_visible():
            print("✓ Terminal restored and visible")
            restored_text = page.locator(".xterm-rows").first.inner_text()
            print(f"Restored content: {restored_text[:300] if restored_text else 'empty'}")

            # Check for previous commands
            has_round1 = "ROUND1" in restored_text or "Hello Terminal" in restored_text
            has_round2 = "ROUND2" in restored_text or "Conversation Test" in restored_text

            if has_round1:
                print("✓ ROUND1 found in restored terminal!")
            else:
                print("ROUND1 NOT found")

            if has_round2:
                print("✓ ROUND2 found in restored terminal!")
            else:
                print("ROUND2 NOT found")
        else:
            print("Terminal not visible after restore")
            restored_text = ""

        page.screenshot(path="/tmp/test_09_history_check.png")

        # Step 11: Third command (continuation)
        print("\n--- Step 11: Third Command (Continuation) ---")
        restored_xterm.click()
        time.sleep(1)
        page.keyboard.type("echo 'ROUND3: After Restore'")
        page.keyboard.press("Enter")
        time.sleep(3)
        print("✓ Sent third command")
        page.screenshot(path="/tmp/test_10_round3.png")

        # Final verification
        print("\n--- Final Verification ---")
        final_text = page.locator(".xterm-rows").first.inner_text()
        print(f"Final content length: {len(final_text)}")

        has_round3 = "ROUND3" in final_text or "After Restore" in final_text
        if has_round3:
            print("✓ ROUND3 found - continuation works!")
        else:
            print("ROUND3 NOT found")

        page.screenshot(path="/tmp/test_11_final.png")

        browser.close()

        # Success criteria: history preserved and continuation works
        history_ok = has_round1 and has_round2
        continuation_ok = has_round3
        result = history_ok and continuation_ok

        print("\n" + "=" * 60)
        if result:
            print("TEST PASSED")
            print("- History preserved: ROUND1 and ROUND2 found")
            print("- Continuation works: ROUND3 found")
        else:
            print("TEST FAILED")
            if not history_ok:
                print("- History NOT preserved")
            if not continuation_ok:
                print("- Continuation NOT working")
        print("=" * 60)
        print("\nScreenshots: /tmp/test_*.png")

        return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    test_terminal_restore_full(headless=HEADLESS)
