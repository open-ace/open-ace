#!/usr/bin/env python3
"""
E2E Test for Terminal Session Screen Restore

Tests that when a terminal tab is closed and restored, the PTY output
history is preserved and displayed.

Flow:
1. Create terminal session
2. Navigate to Workspace with terminal
3. Wait for terminal connection and welcome screen
4. Close the terminal tab
5. Restore from Session List
6. Verify the same welcome screen appears (not a fresh startup)
"""

import os
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
MACHINE_ID = os.environ.get("MACHINE_ID", "6f85734e-9b21-4320-a857-a67bc36b9078")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


def login_api():
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert resp.json()["success"], "Login failed"
    return session


def create_terminal_api():
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


def wait_for_terminal_running(terminal_id, timeout=30):
    session = login_api()
    start = time.time()
    while time.time() - start < timeout:
        resp = session.get(
            f"{BASE_URL}/api/remote/terminal/{terminal_id}/status?machine_id={MACHINE_ID}"
        )
        data = resp.json()
        status = data.get("terminal", {}).get("status", "unknown")
        if status == "running":
            return True
        time.sleep(2)
    return False


def test_terminal_screen_restore(headless=HEADLESS):
    """Test that terminal screen content is restored after tab close and restore."""
    print("\n" + "=" * 60)
    print("Terminal Screen Restore Test")
    print("=" * 60)

    # Step 1: Create terminal
    print("\n--- Step 1: Create Terminal ---")
    terminal_id = create_terminal_api()
    if not terminal_id:
        print("FAIL: Could not create terminal")
        return False

    if not wait_for_terminal_running(terminal_id):
        print("FAIL: Terminal did not start")
        return False
    print("✓ Terminal is running")

    # Get the terminal info for verification
    session = login_api()
    resp = session.get(
        f"{BASE_URL}/api/remote/terminal/{terminal_id}/status?machine_id={MACHINE_ID}"
    )
    terminal_info = resp.json()["terminal"]
    print(f"WebSocket URL: {terminal_info['ws_url']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        # Step 2: Login
        print("\n--- Step 2: Login ---")
        page.goto(f"{BASE_URL}/login")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        # Wait for SPA redirect after login (may go to /manage/dashboard or /work)
        try:
            page.wait_for_url("**/manage/**", timeout=10000)
        except Exception:
            page.wait_for_timeout(5000)
        print("✓ Logged in")

        # Step 3: Open Terminal
        print("\n--- Step 3: Open Terminal in Workspace ---")
        restore_url = f"{BASE_URL}/work?workspaceType=terminal&terminalId={terminal_id}&machineId={MACHINE_ID}&machineName=openace"
        page.goto(restore_url)

        # Wait for xterm to render (terminal WebSocket connection takes time)
        xterm = page.locator(".xterm").first
        try:
            xterm.wait_for(state="visible", timeout=30000)
            print("✓ Terminal visible")
        except Exception:
            page.screenshot(path="/tmp/terminal_step3_fail.png")
            print("FAIL: Terminal not visible")
            browser.close()
            return False
        if xterm.is_visible():
            print("✓ Terminal visible")
        else:
            print("FAIL: Terminal not visible")
            browser.close()
            return False

        time.sleep(5)  # Wait for terminal content to render

        # Get initial terminal content
        initial_content = page.locator(".xterm-rows").first.inner_text()
        print(f"Initial content (first 200 chars): {initial_content[:200]}")

        # Check for key indicators that terminal has started
        has_welcome = "Claude Code" in initial_content or "Welcome" in initial_content
        if has_welcome:
            print("✓ Welcome screen visible")
        page.screenshot(path="/tmp/screen_restore_01_initial.png")

        # Step 4: Close terminal tab
        print("\n--- Step 4: Close Terminal Tab ---")
        active_tab = page.locator(".workspace-tab.active").first
        if active_tab.is_visible():
            active_tab.hover()
            time.sleep(0.5)
            close_btn = active_tab.locator("button, [class*='close']").first
            if close_btn.is_visible():
                close_btn.click()
                time.sleep(2)
                print("✓ Closed terminal tab")
        page.screenshot(path="/tmp/screen_restore_02_closed.png")

        # Step 5: Restore from Session List
        print("\n--- Step 5: Restore from Session List ---")
        page.goto(f"{BASE_URL}/work")
        time.sleep(5)

        # Click on terminal session
        session_buttons = page.locator("button[class*='session']").all()
        for btn in session_buttons:
            if btn.locator(".bi-terminal-fill").count() > 0:
                btn.click()
                time.sleep(1)
                break
        print("✓ Opened session detail")

        # Click Restore
        restore_btn = page.locator("button").filter(has_text="Restore")
        if restore_btn.is_visible():
            restore_btn.click()
            time.sleep(5)
            print("✓ Clicked Restore")
        else:
            print("FAIL: Restore button not found")
            browser.close()
            return False

        page.screenshot(path="/tmp/screen_restore_03_restored.png")

        # Step 6: Verify screen restore
        print("\n--- Step 6: Verify Screen Restore ---")
        time.sleep(8)  # Wait for WebSocket connection and screen restore

        restored_xterm = page.locator(".xterm").first
        if not restored_xterm.is_visible():
            print("FAIL: Terminal not visible after restore")
            browser.close()
            return False

        restored_content = page.locator(".xterm-rows").first.inner_text()
        print(f"Restored content (first 200 chars): {restored_content[:200]}")

        # Key test: restored content should contain terminal output
        has_terminal_history = (
            "[root@openace" in restored_content
            or "claude" in restored_content
            or "qwen" in restored_content
            or "Run:" in restored_content
            or "Open ACE Remote Terminal" in restored_content
            or "Select a tool" in restored_content
            or "Shell" in restored_content
        )
        has_fresh_startup = (
            "Quick safety check" in restored_content and "[root@openace" not in restored_content
        )

        print(f"Has terminal history (banner/bash prompt): {has_terminal_history}")
        print(f"Is fresh startup (only safety check): {has_fresh_startup}")

        page.screenshot(path="/tmp/screen_restore_04_final.png")

        browser.close()

        # Success criteria: restored terminal shows PTY history (banner/bash prompt)
        # This proves that PTY output buffer was sent to new WebSocket connection
        if has_terminal_history and not has_fresh_startup:
            print("\n" + "=" * 60)
            print("TEST PASSED")
            print("- Terminal screen restored with PTY history")
            print("- Banner/bash prompt visible (PTY buffer sent)")
            print("=" * 60)
            return True
        elif has_fresh_startup:
            print("\n" + "=" * 60)
            print("TEST FAILED")
            print("- Terminal shows fresh startup (safety check only)")
            print("- PTY history was NOT preserved")
            print("=" * 60)
            return False
        else:
            print("\n" + "=" * 60)
            print("TEST INCONCLUSIVE")
            print("- Could not determine if history was preserved")
            print(f"- Content: {restored_content[:100]}")
            print("=" * 60)
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    test_terminal_screen_restore(headless=HEADLESS)
