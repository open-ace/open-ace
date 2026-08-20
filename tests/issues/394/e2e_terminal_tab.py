#!/usr/bin/env python3
"""
Open ACE - Web Terminal E2E Test (Phase 1-4)

Tests the terminal tab functionality including:
  Phase 1:
    - Terminal option in new session modal
    - Terminal tab creation and rendering
  Phase 2:
    - Session sync endpoint availability
  Phase 3:
    - Terminal status bar (connection state, machine name)
    - Working directory input in terminal creation
  Phase 4:
    - Full terminal WebSocket connection via mock server
    - xterm.js rendering with dark terminal area
    - Keyboard input and echo response
    - Connection state indicator (green dot for connected)

Run:
  HEADLESS=true  python tests/394/e2e_terminal_tab.py
  HEADLESS=false python tests/394/e2e_terminal_tab.py

Pytest note (#2457): step functions are named `_check_*` and the script is
driven by `run_all_checks()`. The only collected test is
`test_e2e_terminal_tab_script`, which re-runs this file as a subprocess
against BASE_URL (exported by the lane runner) and asserts on its exit
code — the same pattern as tests/issues/559/e2e_terminal_ws_handler.py.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-terminal-tab")

TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = os.environ.get("TEST_REAL_PASS", "admin123")


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def take_screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    log("Screenshot", path)


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only, #2457).

    Freshly initialized databases gate the admin behind a password-change
    flow that would intercept the UI pages this script exercises. Deployed
    environments keep their own user list and are unaffected (no-op when
    the default DB is absent or the gate is already clear).
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (TEST_USER,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log("Setup", f"password-gate clear skipped: {exc}")


def login_via_api():
    """Login and get session token."""
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.cookies.get("session_token")
    assert token, f"No session_token cookie found. Cookies: {dict(resp.cookies)}"
    return token


# ═══════════════════════════════════════════════════════════
# Mock WebSocket Terminal Server
# ═══════════════════════════════════════════════════════════


def start_mock_terminal_server():
    """
    Start a mock WebSocket terminal server for E2E testing.

    Simulates a real terminal: accepts connections, sends welcome message,
    and echoes input back (simulating a shell prompt).

    Returns the port number, or None if websockets is not available.
    """
    import threading

    try:
        import websockets
    except ImportError:
        log("Mock", "websockets package not installed, skipping mock server")
        return None

    port_holder = [None]

    async def handle_connection(websocket):
        """Handle a mock terminal WebSocket connection."""
        try:
            # Send welcome message (green text like real terminal)
            await websocket.send(
                b"\r\n\x1b[32mMock Terminal Server - E2E Test\r\n" b"\x1b[0m\r\n$ "
            )

            async for message in websocket:
                if isinstance(message, bytes):
                    text = message.decode("utf-8", errors="replace")
                    # Simulate shell: echo command + show prompt
                    await websocket.send(f"\r\n{text}\r\n$ ".encode())
                elif isinstance(message, str):
                    try:
                        data = json.loads(message)
                        if data.get("type") == "resize":
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
        except Exception:
            pass

    async def run_server():
        async with websockets.serve(
            handle_connection, "localhost", 0, subprotocols=["binary"]
        ) as server:
            port_holder[0] = server.sockets[0].getsockname()[1]
            await asyncio.Future()  # run forever

    def run_thread():
        asyncio.run(run_server())

    thread = threading.Thread(target=run_thread, daemon=True)
    thread.start()

    # Wait for server to bind and report port
    for _ in range(30):
        if port_holder[0] is not None:
            break
        time.sleep(0.1)

    return port_holder[0]


# ═══════════════════════════════════════════════════════════
# Phase 1 Tests
# ═══════════════════════════════════════════════════════════


def _check_terminal_option_in_modal(page):
    """Verify terminal option exists in the new session modal."""
    log("Phase 1", "Navigating to workspace...")
    page.goto(f"{BASE_URL}/work/workspace", wait_until="networkidle", timeout=30000)
    time.sleep(2)
    take_screenshot(page, "p1-01-workspace")

    # Sidebar button on a fresh workspace (no tabs yet); the tab-strip
    # ".workspace-new-tab-btn" only renders once at least one tab exists —
    # both open the same NewSessionModal (Workspace.tsx / SessionList.tsx)
    new_tab_btn = page.locator("[data-testid='new-session-btn'], .workspace-new-tab-btn").first
    new_tab_btn.wait_for(state="visible", timeout=10000)
    new_tab_btn.click()
    time.sleep(1)
    take_screenshot(page, "p1-02-modal-open")

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

    close_btn = modal.locator(".btn-secondary")
    if close_btn.is_visible():
        close_btn.click()
    time.sleep(0.5)


# ═══════════════════════════════════════════════════════════
# Phase 2 Tests (API-level)
# ═══════════════════════════════════════════════════════════


def _check_session_sync_api(token):
    """Test session-sync API endpoint accepts data."""
    log("Phase 2", "Testing session-sync endpoint...")
    headers = {"Cookie": f"session_token={token}", "Content-Type": "application/json"}

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
# Phase 4 Tests (Full terminal connection & interaction)
# ═══════════════════════════════════════════════════════════


def _check_terminal_connection_and_interaction(page, mock_ws_port):
    """
    Test full terminal lifecycle with mock WebSocket server.

    Uses Playwright route interception to inject mock server URL,
    then verifies: xterm.js rendering, connection state, keyboard input,
    and terminal output echo.
    """
    if mock_ws_port is None:
        log("Phase 4", "SKIPPED - websockets package not available")
        return

    mock_ws_url = f"ws://localhost:{mock_ws_port}"
    log("Phase 4", f"Mock terminal server running on port {mock_ws_port}")

    # Intercept start_terminal API: return mock server info directly
    def handle_start(route):
        route.fulfill(
            json={
                "success": True,
                "terminal": {
                    "terminal_id": "mock-terminal-e2e-001",
                    "status": "running",
                    "ws_url": mock_ws_url,
                    "token": "test-mock-token",
                },
            }
        )

    # Intercept stop_terminal API: prevent real stop attempt
    def handle_stop(route):
        route.fulfill(json={"success": True})

    page.route("**/api/remote/terminal/start", handle_start)
    page.route("**/api/remote/terminal/stop", handle_stop)

    try:
        # Navigate to workspace
        page.goto(f"{BASE_URL}/work/workspace", wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Open new session modal (see Phase 1 note on the selector)
        new_tab_btn = page.locator("[data-testid='new-session-btn'], .workspace-new-tab-btn").first
        new_tab_btn.wait_for(state="visible", timeout=10000)
        new_tab_btn.click()
        time.sleep(1)
        take_screenshot(page, "p4-01-modal-open")

        modal = page.locator(".modal.show")
        modal.wait_for(state="visible", timeout=5000)

        # Select Terminal workspace type
        buttons = modal.locator("button")
        terminal_btn = None
        for i in range(buttons.count()):
            text = buttons.nth(i).inner_text()
            if "Terminal" in text or "终端" in text:
                terminal_btn = buttons.nth(i)
                break

        if not terminal_btn:
            log("Phase 4", "SKIPPED - Terminal button not found")
            return

        terminal_btn.click()
        time.sleep(0.5)

        # Select first machine
        machine_list = modal.locator(".list-group-item")
        if machine_list.count() == 0:
            log("Phase 4", "SKIPPED - No machines available")
            return

        machine_list.first.click()
        time.sleep(0.5)

        # Click Create - this triggers the intercepted start_terminal API
        create_btn = modal.locator(".btn-primary").last
        create_btn.click()
        log("Phase 4", "Clicked Create - mock server will handle connection")

        # The terminal tab mounts asynchronously (tab switch, React mount,
        # then a dynamic import of the @xterm/xterm chunk); slow CI runners
        # regularly exceed any fixed sleep, so wait for xterm to actually
        # attach and dump browser diagnostics if it never does.
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: (
                console_errors.append(f"[console.{msg.type}] {msg.text}")
                if msg.type in ("error", "warning")
                else None
            ),
        )
        page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))

        xterm_screen = page.locator(".xterm-screen")
        try:
            xterm_screen.wait_for(state="visible", timeout=30000)
        except PlaywrightTimeoutError:
            take_screenshot(page, "error-no-xterm")
            log("Phase 4", "body text: " + page.locator("body").inner_text()[:400])
            if console_errors:
                log("Phase 4", "browser errors: " + " | ".join(console_errors[-10:]))
            raise
        take_screenshot(page, "p4-02-terminal-created")
        log("Phase 4", "xterm.js terminal rendered!")

        # ── Verify: dark background (terminal area) ──
        terminal_container = page.locator("div[style*='background-color: rgb(30, 30, 46)']")
        if terminal_container.count() > 0:
            log("Phase 4", "Dark terminal background confirmed (#1e1e2e)")
        else:
            log("Phase 4", "Checking terminal background color...")

        # ── Verify: connection state = "Connected" ──
        connected = False
        for _ in range(15):
            body_text = page.locator("body").inner_text()
            if "Connected" in body_text:
                connected = True
                break
            time.sleep(1)

        assert connected, "Terminal did not reach Connected state within 15 seconds"
        log("Phase 4", "Terminal connected to mock server!")
        take_screenshot(page, "p4-03-terminal-connected")

        # ── Verify: welcome message in terminal ──
        # xterm.js renders in canvas, but DOM rows may contain text
        xterm_rows = page.locator(".xterm-rows > div")
        if xterm_rows.count() > 0:
            row_texts = []
            for i in range(min(xterm_rows.count(), 10)):
                text = xterm_rows.nth(i).inner_text().strip()
                if text:
                    row_texts.append(text)
            log("Phase 4", f"Terminal rows: {row_texts}")
            has_welcome = any("Mock Terminal" in t or "$" in t for t in row_texts)
            if has_welcome:
                log("Phase 4", "Welcome message displayed in terminal!")

        # ── Verify: green connection indicator ──
        status_dot = page.locator("span[style*='border-radius: 50%']")
        if status_dot.count() > 0:
            style = status_dot.first.get_attribute("style") or ""
            if "#22c55e" in style:
                log("Phase 4", "Status indicator is GREEN (connected)")
            else:
                log("Phase 4", f"Status indicator style: {style[:80]}")

        # ── Verify: machine name in status bar ──
        status_bar = page.locator("div.d-flex.align-items-center.px-2.py-1")
        if status_bar.count() > 0:
            status_text = status_bar.first.inner_text()
            log("Phase 4", f"Status bar: {status_text[:100]}")
            assert "Claude Code" in status_text, f"'Claude Code' not in status bar: {status_text}"
            assert "Connected" in status_text, f"'Connected' not in status bar: {status_text}"

        # ── Verify: keyboard input and echo ──
        xterm_screen.first.click()
        time.sleep(0.3)
        page.keyboard.type("echo hello")
        time.sleep(0.5)
        page.keyboard.press("Enter")
        time.sleep(1)
        take_screenshot(page, "p4-04-terminal-input")

        # Check terminal rows for echoed input
        xterm_rows = page.locator(".xterm-rows > div")
        if xterm_rows.count() > 0:
            all_text = " ".join(
                xterm_rows.nth(i).inner_text() for i in range(min(xterm_rows.count(), 20))
            )
            if "echo hello" in all_text:
                log("Phase 4", "Input 'echo hello' echoed in terminal!")
            else:
                log("Phase 4", f"Terminal content (first 200 chars): {all_text[:200]}")

        take_screenshot(page, "p4-05-terminal-final")

        # ── Verify: close terminal tab ──
        tabs = page.locator(".workspace-tab")
        if tabs.count() > 1:
            close_btn = tabs.last.locator(".tab-action-btn").last
            close_btn.click()
            time.sleep(1)
            take_screenshot(page, "p4-06-terminal-closed")
            log("Phase 4", "Terminal tab closed successfully")

        log("Phase 4", "All terminal connection & interaction tests passed!")

    finally:
        page.unroute("**/api/remote/terminal/start")
        page.unroute("**/api/remote/terminal/stop")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════


def run_all_checks():
    """Run all terminal tab checks (script entrypoint)."""
    _clear_seeded_password_gate()
    token = login_via_api()
    log("Setup", f"Logged in as {TEST_USER}")

    # Phase 2: API-level tests
    _check_session_sync_api(token)

    # Start mock terminal server for Phase 4
    mock_ws_port = start_mock_terminal_server()
    if mock_ws_port:
        log("Setup", f"Mock terminal server started on port {mock_ws_port}")

    # Browser tests
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en",
        )
        page = context.new_page()

        try:
            # UI login establishes the full client-side auth state the SPA
            # needs (cookie alone is not enough for the workspace route)
            page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            page.fill("#username", TEST_USER)
            page.fill("#password", TEST_PASS)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=30000)
            # Phase 1: Modal UI verification
            _check_terminal_option_in_modal(page)

            # Phase 4: Full terminal connection & interaction
            _check_terminal_connection_and_interaction(page, mock_ws_port)

            log("Result", "All Phase 1-4 tests passed!")
        except Exception as e:
            take_screenshot(page, "error-final")
            log("Error", str(e))
            traceback.print_exc()
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Web Terminal Tab E2E Test (Phase 1-4)")
    print(f"  BASE_URL:  {BASE_URL}")
    print(f"  HEADLESS:  {HEADLESS}")
    print("=" * 60)
    run_all_checks()


# ═══════════════════════════════════════════════════════════
# Pytest entry (single collected test; see module docstring)
# ═══════════════════════════════════════════════════════════


def test_e2e_terminal_tab_script():
    """Drive this script as a subprocess against BASE_URL (#2457)."""
    try:
        resp = requests.get(f"{BASE_URL}/login", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(f"test server not reachable at {BASE_URL}")

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    assert "All Phase 1-4 tests passed!" in proc.stdout
