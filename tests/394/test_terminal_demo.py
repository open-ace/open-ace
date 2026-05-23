#!/usr/bin/env python3
"""
Terminal WebSocket Demo Test - Issue #394
Tests the WebSocket proxy connection for web terminal feature.
"""

import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")


def login(page):
    """Login to Open ACE"""
    page.goto(f"{BASE_URL}/login")
    page.wait_for_selector("#username", timeout=5000)
    page.fill("#username", "黄迎春")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    page.wait_for_url("**/work", timeout=10000)
    print("✓ Login successful")


def get_machine_token():
    """Get machine registration token"""
    resp = requests.get(
        f"{BASE_URL}/api/remote/machines", cookies={"session_token": "test-admin-token"}
    )
    data = resp.json()
    machines = data.get("machines", [])
    for m in machines:
        if "192.168.64.3" in m.get("ip", ""):
            return m.get("token", "")
    return ""


def create_terminal_session(page, machine_id: str):
    """Create a terminal session via UI"""
    # Navigate to work page
    page.goto(f"{BASE_URL}/work")
    page.wait_for_selector(".workspace-container", timeout=5000)

    # Click "New Tab" button
    new_tab_btn = page.locator("button:has-text('New Tab')")
    if new_tab_btn.count() > 0:
        new_tab_btn.click()
        time.sleep(1)

    # Select terminal workspace type (if dropdown exists)
    workspace_type_select = page.locator("select[id*='workspaceType']")
    if workspace_type_select.count() > 0:
        workspace_type_select.select_option("terminal")

    # Select machine (if dropdown exists)
    machine_select = page.locator("select[id*='machineId']")
    if machine_select.count() > 0:
        machine_select.select_option(machine_id)

    # Click create button
    create_btn = page.locator("button:has-text('Create')")
    if create_btn.count() > 0:
        create_btn.click()
        time.sleep(2)


import os

import pytest


@pytest.mark.skip(reason="Requires live machine_id fixture - run standalone via main()")
def test_terminal(page, machine_id: str):
    """Test terminal WebSocket connection"""
    # Navigate to work page with terminal params
    url = f"{BASE_URL}/work?workspaceType=terminal&machineId={machine_id}&machineName=openace"
    page.goto(url)
    page.wait_for_load_state("networkidle", timeout=15000)
    print("✓ Work page loaded with terminal params")

    # Wait for terminal to appear
    time.sleep(3)

    # Check terminal container for xterm.js
    terminal_container = page.locator(".xterm")

    if terminal_container.count() > 0:
        print("✓ Terminal container found")
    else:
        print("✗ Terminal container NOT found")
        page.screenshot(path="/tmp/terminal_test_no_xterm.png")
        return False

    # Wait for connection
    time.sleep(5)

    # Check console for WebSocket messages
    console_logs = []
    page.on("console", lambda msg: console_logs.append(msg.text))

    # Wait more for WebSocket to connect
    time.sleep(3)

    # Look for "Connected" in status or console logs
    status_text = page.locator("text=Connected, text=已连接").first
    if status_text.count() > 0:
        print("✓ Terminal shows Connected status!")
        page.screenshot(path="/tmp/terminal_connected.png")
        return True

    # Check console logs for WebSocket messages
    ws_logs = [log for log in console_logs if "WebSocket" in log or "Terminal" in log]
    for log in ws_logs[-10:]:
        print(f"  Console: {log}")

    page.screenshot(path="/tmp/terminal_final_state.png")

    # Check if there's any error message
    error_text = page.locator("text=Error, text=错误, text=Disconnected")
    if error_text.count() > 0:
        print(f"✗ Terminal shows error/disconnected: {error_text.first.text_content()}")
        return False

    return True


def main():
    print("=" * 50)
    print("Terminal WebSocket Demo Test")
    print("=" * 50)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Login
            login(page)

            # Get machine ID
            resp = requests.get(
                f"{BASE_URL}/api/auth/login", json={"username": "黄迎春", "password": "admin123"}
            )
            # Get session token from cookies
            session_token = None
            for cookie in page.context.cookies():
                if cookie["name"] == "session_token":
                    session_token = cookie["value"]
                    break

            resp = requests.get(
                f"{BASE_URL}/api/remote/machines", cookies={"session_token": session_token}
            )
            machines = resp.json().get("machines", [])

            # Find the real machine
            machine_id = None
            for m in machines:
                if "192.168.64.3" in m.get("ip_address", ""):
                    machine_id = m.get("machine_id")
                    print(f"Found machine: {m.get('machine_name')} (ID: {machine_id})")
                    break

            # Test terminal
            success = test_terminal(page, machine_id)

            if success:
                print("\n✓ TERMINAL TEST PASSED!")
            else:
                print("\n✗ TERMINAL TEST FAILED")

            # Keep browser open for demo
            print("\nKeeping browser open for 30 seconds...")
            time.sleep(30)

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="/tmp/terminal_error.png")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
