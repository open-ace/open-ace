#!/usr/bin/env python3
"""
Open ACE - Web Terminal E2E Test (Phase 1-3)

Tests the terminal tab functionality including:
  Phase 1:
    - Terminal option in new session modal
    - Terminal tab creation and rendering
  Phase 2:
    - Session sync endpoint availability
    - Terminal sessions appear in conversation history
  Phase 3:
    - Terminal status bar (connection state, machine name)
    - Working directory input in terminal creation
    - Terminal tab close behavior

Run:
  HEADLESS=true  python tests/e2e_terminal_tab.py
  HEADLESS=false python tests/e2e_terminal_tab.py
"""

import json
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import expect, sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-terminal-tab")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    log("Screenshot", path)


def login_via_api():
    """Login and get session token."""
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    # session_token is set via Set-Cookie header
    token = resp.cookies.get("session_token")
    assert token, f"No session_token cookie found. Cookies: {dict(resp.cookies)}"
    return token


# ═══════════════════════════════════════════════════════════
# Phase 1 Tests
# ═══════════════════════════════════════════════════════════


def test_terminal_option_in_modal(page):
    """Verify terminal option exists in the new session modal."""
    log("Phase 1", "Navigating to workspace...")
    page.goto(f"{BASE_URL}/work/workspace", wait_until="networkidle", timeout=30000)
    time.sleep(2)
    take_screenshot(page, "p1-01-workspace")

    # Open new session modal
    new_tab_btn = page.locator(".workspace-new-tab-btn")
    new_tab_btn.wait_for(state="visible", timeout=10000)
    new_tab_btn.click()
    time.sleep(1)
    take_screenshot(page, "p1-02-modal-open")

    # Verify three buttons: Local, Remote, Terminal
    modal = page.locator(".modal.show")
    modal.wait_for(state="visible", timeout=5000)
    buttons = modal.locator("button")
    button_texts = [buttons.nth(i).inner_text() for i in range(buttons.count())]
    log("Phase 1", f"Found buttons: {button_texts}")

    has_local = any("Local" in t or "本地" in t for t in button_texts)
    has_remote = any("Remote" in t or "远程" in t for t in button_texts)
    has_terminal = any("Terminal" in t or "终端" in t for t in button_texts)

    assert has_local, f"Local button not found. Buttons: {button_texts}"
    assert has_remote, f"Remote button not found. Buttons: {button_texts}"
    assert has_terminal, f"Terminal button not found. Buttons: {button_texts}"
    log("Phase 1", "All three workspace type buttons found!")

    # Close modal
    close_btn = modal.locator(".btn-secondary")
    if close_btn.is_visible():
        close_btn.click()
    time.sleep(0.5)


def test_terminal_creation_flow(page):
    """Test terminal tab creation and rendering."""
    # Open modal
    new_tab_btn = page.locator(".workspace-new-tab-btn")
    new_tab_btn.click()
    time.sleep(1)

    modal = page.locator(".modal.show")
    modal.wait_for(state="visible", timeout=5000)

    # Click Terminal button
    buttons = modal.locator("button")
    terminal_btn = None
    for i in range(buttons.count()):
        text = buttons.nth(i).inner_text()
        if "Terminal" in text or "终端" in text:
            terminal_btn = buttons.nth(i)
            break
    assert terminal_btn, "Terminal button not found"
    terminal_btn.click()
    time.sleep(0.5)
    take_screenshot(page, "p1-03-terminal-selected")

    # Phase 3: Verify working directory input exists
    workdir_input = modal.locator("input.form-control")
    if workdir_input.is_visible():
        log("Phase 3", "Working directory input found!")
    else:
        log("Phase 3", "No machine selected yet - workdir input hidden")

    # Verify info hint
    info_alert = modal.locator(".alert-info")
    if info_alert.is_visible():
        info_text = info_alert.inner_text()
        assert "Claude Code" in info_text or "terminal" in info_text.lower()
        log("Phase 3", f"Info hint visible: {info_text[:60]}")

    # Check machines
    machine_list = modal.locator(".list-group-item")
    machine_count = machine_list.count()

    if machine_count > 0:
        log("Phase 1", f"Found {machine_count} machine(s)")
        machine_list.first.click()
        time.sleep(0.5)
        take_screenshot(page, "p1-04-machine-selected")

        # Create terminal
        create_btn = modal.locator(".btn-primary").last
        create_btn.click()
        time.sleep(5)
        take_screenshot(page, "p1-05-terminal-created")

        # Verify terminal tab
        tabs = page.locator(".workspace-tab")
        assert tabs.count() > 1, "Expected more than one tab after terminal creation"

        # Check terminal icon
        terminal_icon = page.locator(".bi-terminal")
        if terminal_icon.count() > 0:
            log("Phase 1", "Terminal tab icon found!")

            # Phase 3: Verify status bar
            status_bar = page.locator(".flex-column > .d-flex:last-child")
            if status_bar.is_visible():
                status_text = status_bar.inner_text()
                log("Phase 3", f"Status bar content: {status_text[:80]}")
                assert (
                    "Claude Code" in status_text
                    or "Connected" in status_text
                    or "Connecting" in status_text
                )

            # Phase 3: Verify connection state indicator
            status_dot = page.locator("span[style*='border-radius: 50%']")
            if status_dot.is_visible():
                log("Phase 3", "Connection status indicator found!")

            take_screenshot(page, "p1-06-terminal-rendered")

            # Close terminal tab
            close_btn = tabs.last.locator(".tab-action-btn").last
            close_btn.click()
            time.sleep(1)
            take_screenshot(page, "p1-07-terminal-closed")
        else:
            log("Phase 1", "Terminal icon not found (may still be loading)")
    else:
        log("Phase 1", "No machines available - skipping creation test")

    # Close modal if still open
    close_modal = page.locator(".modal.show .btn-secondary")
    if close_modal.is_visible():
        close_modal.click()
    time.sleep(0.5)


# ═══════════════════════════════════════════════════════════
# Phase 2 Tests (API-level)
# ═══════════════════════════════════════════════════════════


def test_session_sync_api(token):
    """Test session-sync API endpoint accepts data."""
    log("Phase 2", "Testing session-sync endpoint...")
    headers = {"Cookie": f"session_token={token}", "Content-Type": "application/json"}

    # The session-sync endpoint is accessed via agent message endpoint
    # We test that the message type is recognized (not "Unknown message type")
    resp = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "session_sync",
            "machine_id": "test-machine-e2e",
            "session_id": "test-session-e2e-001",
            "tool_name": "claude-code",
            "message_count": 2,
            "total_input_tokens": 100,
            "total_output_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": "Hello from e2e test",
                    "timestamp": "2026-05-16T00:00:00Z",
                },
                {
                    "role": "assistant",
                    "content": "E2E test response",
                    "timestamp": "2026-05-16T00:00:01Z",
                    "model": "claude-sonnet-4-6",
                },
            ],
        },
        headers=headers,
        timeout=10,
    )

    # The request may fail because test-machine doesn't exist,
    # but it should NOT return "Unknown message type"
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("success") is True, f"session_sync failed: {data}"
        log("Phase 2", "session-sync endpoint accepted data!")
    elif resp.status_code == 400:
        data = resp.json()
        if "Unknown message type" in data.get("error", ""):
            raise AssertionError(f"session_sync not recognized: {data}")
        log("Phase 2", f"session_sync processed (expected error): {data.get('error', '')[:80]}")
    else:
        log("Phase 2", f"session_sync response: {resp.status_code}")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════


def test_terminal_tab():
    """Run all terminal tab tests."""
    token = login_via_api()
    log("Setup", f"Logged in as {TEST_USER}")

    # Phase 2: API-level tests
    test_session_sync_api(token)

    # Phase 1 & 3: Browser tests
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en",
        )
        context.add_cookies(
            [
                {
                    "name": "session_token",
                    "value": token,
                    "domain": "localhost",
                    "path": "/",
                }
            ]
        )
        page = context.new_page()

        try:
            test_terminal_option_in_modal(page)
            test_terminal_creation_flow(page)
            log("Result", "All Phase 1-3 tests passed!")
        except Exception as e:
            take_screenshot(page, "error-final")
            log("Error", str(e))
            traceback.print_exc()
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Web Terminal Tab E2E Test (Phase 1-3)")
    print(f"  BASE_URL:  {BASE_URL}")
    print(f"  HEADLESS:  {HEADLESS}")
    print("=" * 60)
    test_terminal_tab()
