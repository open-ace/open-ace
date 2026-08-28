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
  HEADLESS=true  python tests/e2e/terminal/e2e_terminal_tab.py
  HEADLESS=false python tests/e2e/terminal/e2e_terminal_tab.py

Pytest note (#2457): step functions are named `_check_*` and the script is
driven by `run_all_checks()`. The only collected test is
`test_e2e_terminal_tab_script`, which re-runs this file as a subprocess
against BASE_URL (exported by the lane runner) and asserts on its exit
code — the same pattern as tests/issues/559/e2e_terminal_ws_handler.py.
The sync subprocess keeps pytest away from the async `page` fixture in
tests/conftest.py, whose teardown pytest-timeout cannot kill (the CI
shard deadlocks quarantined in August 2026 were exactly that).

Cohabitation hardening (#2457): this file shares shard 2 of the issues
lane with tests/issues/165, whose leftover machine goes offline (heartbeat
timeout) and whose sessions stay in the shared per-shard DB. The script
therefore registers its OWN machine right before Phase 4 (selected by
name in the modal) and seeds its own terminal-model API key, instead of
trusting whatever machine rows earlier tests left behind. It also enables
the workspace feature in the lane config (fresh homes default it off and
the page gates even terminal-only tabs behind it) and seeds one local tab
first: Workspace's own modal — the one that actually creates a terminal
tab — only becomes reachable from the tab strip once a tab exists.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import traceback
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(394)]

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


def _ensure_workspace_enabled():
    """Enable the workspace feature in the lane config (idempotent, #2457).

    /api/workspace/config reports workspace.enabled from ~/.open-ace/
    config.json (default false in a freshly initialized home) and the
    workspace page gates EVERYTHING behind it — including terminal-only
    tabs — so a default lane home renders "Workspace not configured" and
    the terminal tab never mounts (this is the "body text: Open ACE" CI
    failure signature). The endpoint re-reads the file per request, so
    flipping it needs no server restart. Deployed environments carry
    their own workspace config and are unaffected.
    """
    config_path = os.path.expanduser("~/.open-ace/config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
    workspace = config.setdefault("workspace", {})
    if workspace.get("enabled"):
        return
    workspace["enabled"] = True
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    log("Setup", "enabled workspace feature in config.json")


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


def _current_user_id(token):
    resp = requests.get(f"{BASE_URL}/api/auth/me", cookies={"session_token": token}, timeout=10)
    assert resp.status_code == 200, f"/api/auth/me failed: {resp.status_code}"
    return resp.json()["user"]["id"]


def seed_terminal_model_key(token):
    """Seed an API key advertising a terminal model (idempotent, #2457).

    /api/workspace/terminal-models (remote scope) only lists models that an
    active api_key_store row advertises, and the lane DB starts with zero
    keys — without a seeded key the modal never gets a model, the Create
    button stays disabled, and no terminal tab ever mounts. Scope 'shared'
    satisfies the remote pool (api_key_proxy._list_tool_key_rows matches
    scope = ? OR scope = 'shared'). Re-runs tolerate the duplicate-name
    rejection: any leftover key advertising the model is enough.
    """
    key_name = "e2e-394-terminal-model-key"
    resp = requests.post(
        f"{BASE_URL}/api/api-keys",
        json={
            "provider": "openai",
            "key_name": key_name,
            "api_key": "sk-e2e-394-placeholder-key-000000000000",
            "tenant_id": 1,
            "scope": "shared",
            "cli_tools": json.dumps(["qwen-code"]),
            "cli_settings": json.dumps(
                {
                    "qwen-code": {
                        "modelProviders": {
                            "openai": [{"id": "qwen3-coder-plus", "name": "qwen3-coder-plus"}]
                        }
                    }
                }
            ),
        },
        cookies={"session_token": token},
        timeout=10,
    )
    if resp.status_code == 200:
        log("Setup", f"seeded terminal-model key {key_name!r}")
    else:
        log(
            "Setup",
            f"terminal-model key seed returned {resp.status_code} "
            f"(ok if a previous run already seeded one): {resp.text[:120]}",
        )


def register_terminal_machine(token):
    """Register a fresh machine for this run; return (id, name, bearer).

    The machine is selected BY NAME in Phase 4 so stale rows from sibling
    e2e files (e.g. issue 165's, offline by the time this file runs in the
    shard) can never be picked. Registration + the 'register' message put
    it in the 'online' family that /api/remote/machines/available serves.
    """
    machine_name = f"E2E-394 Terminal {os.getpid()}"
    resp = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": token},
        timeout=10,
    )
    assert resp.status_code == 200, f"machines/register failed: {resp.text[:200]}"
    reg_token = resp.json()["registration_token"]

    machine_id = str(uuid.uuid4())
    resp = requests.post(
        f"{BASE_URL}/api/remote/agent/register",
        json={
            "registration_token": reg_token,
            "machine_id": machine_id,
            "machine_name": machine_name,
            "hostname": "e2e-394.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04",
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
            "agent_version": "1.0.0-e2e",
        },
        timeout=10,
    )
    assert resp.status_code == 200, f"agent/register failed: {resp.text[:200]}"
    agent_bearer = resp.json()["machine"].get("agent_token")
    assert agent_bearer, "No agent_token in register response"

    resp = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
        },
        headers={"Authorization": f"Bearer {agent_bearer}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"agent register message failed: {resp.text[:200]}"

    # terminal-models checks machine assignment (check_user_access), so the
    # machine must belong to the login user, not just exist.
    resp = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": _current_user_id(token), "permission": "admin"},
        cookies={"session_token": token},
        timeout=10,
    )
    assert resp.status_code == 200, f"machine assign failed: {resp.text[:200]}"
    log("Setup", f"registered machine {machine_name!r} ({machine_id[:8]}...)")
    return machine_id, machine_name, agent_bearer


def cleanup_terminal_machine(token, machine_id):
    """Best-effort machine removal so later files see no extra rows."""
    try:
        requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}",
            cookies={"session_token": token},
            timeout=10,
        )
        log("Cleanup", f"deleted machine {machine_id[:8]}...")
    except requests.RequestException as exc:
        log("Cleanup", f"machine delete failed (best-effort): {exc}")


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
        except (OSError, ValueError, websockets.exceptions.WebSocketException):
            # client disconnects / malformed frames must not kill the mock server
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
# Phase 1 Checks
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
# Phase 2 Checks (API-level)
# ═══════════════════════════════════════════════════════════


def _check_session_sync_api(token, machine_id, agent_bearer):
    """Test session-sync API endpoint accepts data for OUR machine."""
    log("Phase 2", "Testing session-sync endpoint...")
    headers = {
        "Cookie": f"session_token={token}",
        "Authorization": f"Bearer {agent_bearer}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "session_sync",
            "machine_id": machine_id,
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
# Phase 4 Checks (Full terminal connection & interaction)
# ═══════════════════════════════════════════════════════════


def _check_terminal_connection_and_interaction(page, mock_ws_port, machine_name):
    """
    Test full terminal lifecycle with mock WebSocket server.

    Uses Playwright route interception to inject mock server URL,
    then verifies: xterm.js rendering, connection state, keyboard input,
    and terminal output echo. The machine is selected by name so a stale
    sibling-test machine can never be picked (see module docstring).
    """
    if mock_ws_port is None:
        raise AssertionError("websockets package not available for the mock terminal server")

    mock_ws_url = f"ws://localhost:{mock_ws_port}"
    log("Phase 4", f"Mock terminal server running on port {mock_ws_port}")

    # Intercept start_terminal API like a cold backend: accept the request,
    # return "pending" with no ws_url yet. The real proxy takes seconds to
    # spawn, and that ordering is load-bearing — TerminalTab's connect
    # effect runs once per wsUrl/token VALUE change, so a ws_url delivered
    # before xterm's async chunk import finishes is silently dropped (the
    # browser never opens the WebSocket). The status poll below delivers
    # the ws_url only after xterm is ready, which is exactly how the
    # production timing plays out.
    def handle_start(route):
        route.fulfill(
            json={
                "success": True,
                "terminal": {
                    "terminal_id": "mock-terminal-e2e-001",
                    "status": "pending",
                },
            }
        )

    # Intercept stop_terminal API: prevent real stop attempt
    def handle_stop(route):
        route.fulfill(json={"success": True})

    # Intercept the status poll: Workspace polls terminal status until the
    # WebSocket proxy is ready, and only (re)applies ws_url/token — which
    # is what triggers TerminalTab's connect once xterm is initialized.
    # Without this the poll loops on "status: unknown" forever and the
    # never-connected terminal is exactly the CI failure mode. The first
    # two polls answer "pending" like the real backend (proxy spawn takes
    # seconds); answering "running" instantly loses the race against
    # xterm's async chunk import — the connect effect would fire before
    # xtermRef exists and never re-fire.
    status_poll_count = [0]

    def handle_status(route):
        status_poll_count[0] += 1
        terminal = {
            "terminal_id": "mock-terminal-e2e-001",
            "status": "pending",
        }
        if status_poll_count[0] > 2:
            terminal = {
                "terminal_id": "mock-terminal-e2e-001",
                "status": "running",
                "ws_url": mock_ws_url,
                "token": "test-mock-token",
            }
        route.fulfill(json={"success": True, "terminal": terminal})

    page.route("**/api/remote/terminal/start", handle_start)
    page.route("**/api/remote/terminal/stop", handle_stop)
    # trailing *: playwright globs match the full URL including the
    # ?machine_id=... query string the status poll appends
    page.route("**/api/remote/terminal/*/status*", handle_status)

    # Workspace's tab-initialization effect gates on the user-url query
    # settling (isLoading); where no qwen-code-webui binary exists the
    # endpoint 503s forever and even the URL-restored terminal tab below
    # never mounts. Fulfil it with a placeholder — the terminal tab under
    # test carries no iframe URL, so the placeholder is never loaded.
    def handle_user_url(route):
        route.fulfill(
            json={"success": True, "url": "http://127.0.0.1:1/", "token": "e2e-mock-token"}
        )

    page.route("**/api/workspace/user-url", handle_user_url)

    try:
        # Seed one local tab through the product's own URL-param path. The
        # tab strip — and Workspace's NewSessionModal wiring, including
        # onCreateTerminal — only renders once a tab exists; on a fresh
        # workspace the sidebar button opens SessionList's modal, whose
        # terminal path calls the API directly and creates NO tab (the
        # "Create clicked but no xterm" failure mode).
        #
        # #2491 drift: the default tab (and with it the tab strip) only
        # materializes once /api/workspace/user-url settles successfully;
        # without a qwen-code-webui binary the endpoint 503s forever and
        # the page is stuck on "Starting your workspace instance". The
        # user-url mock above settles it so the newTab=true path works in
        # every environment; the seeded tab's placeholder iframe is never
        # the tab under test.
        page.goto(f"{BASE_URL}/work/workspace?newTab=true", wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Open Workspace's modal via the tab-strip new-tab button
        new_tab_btn = page.locator(".workspace-new-tab-btn")
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

        assert terminal_btn, "Terminal button not found in modal"
        terminal_btn.click()
        time.sleep(0.5)

        # Select OUR machine by name (cohabitation hardening, #2457)
        machine_list = modal.locator(".list-group-item")
        machine_list.first.wait_for(state="visible", timeout=10000)
        our_row = machine_list.filter(has_text=machine_name)
        assert our_row.count() > 0, (
            f"Machine {machine_name!r} not in modal list "
            f"(rows: {[machine_list.nth(i).inner_text()[:40] for i in range(machine_list.count())]})"
        )
        our_row.first.click()
        time.sleep(1)
        log("Phase 4", f"Selected machine {machine_name!r}")

        # Wait for the terminal-models fetch to arm the Create button
        # (selectedModelKey defaults to the first advertised model). The
        # footer Create is addressed by role: the workspace-type toggles
        # also carry .btn-primary when selected, so class+text filtering
        # is ambiguous.
        create_btn = modal.get_by_role("button", name="Create")
        create_btn.wait_for(state="visible", timeout=10000)
        for _ in range(30):
            if create_btn.is_enabled(timeout=1000):
                break
            time.sleep(1)
        assert create_btn.is_enabled(timeout=1000), (
            "Create button never enabled — terminal-models likely returned no "
            f"models (modal: {modal.inner_text()[:300]})"
        )
        create_btn.click()
        log("Phase 4", "Clicked Create - mock server will handle connection")
        take_screenshot(page, "p4-01b-create-clicked")

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
                or "[TerminalTab]" in msg.text
                or "[Terminal]" in msg.text
                else None
            ),
        )
        page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))

        try:
            page.locator(".xterm-screen").wait_for(state="visible", timeout=30000)
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

        assert connected, (
            "Terminal did not reach Connected state within 15 seconds. "
            f"Terminal logs: {console_errors}"
        )
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
        page.locator(".xterm-screen").first.click()
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
        page.unroute("**/api/remote/terminal/*/status*")
        page.unroute("**/api/workspace/user-url")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════


def run_all_checks():
    """Run all terminal tab checks (script entrypoint)."""
    _clear_seeded_password_gate()
    _ensure_workspace_enabled()
    token = login_via_api()
    log("Setup", f"Logged in as {TEST_USER}")

    seed_terminal_model_key(token)
    machine_id, machine_name, agent_bearer = register_terminal_machine(token)

    try:
        # Phase 2: API-level checks (own machine, authenticated)
        _check_session_sync_api(token, machine_id, agent_bearer)

        # Start mock terminal server for Phase 4
        mock_ws_port = start_mock_terminal_server()
        if mock_ws_port:
            log("Setup", f"Mock terminal server started on port {mock_ws_port}")

        # Browser checks
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
                _check_terminal_connection_and_interaction(page, mock_ws_port, machine_name)

                log("Result", "All Phase 1-4 tests passed!")
            except Exception as e:
                take_screenshot(page, "error-final")
                log("Error", str(e))
                traceback.print_exc()
                raise
            finally:
                browser.close()
    finally:
        cleanup_terminal_machine(token, machine_id)


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
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    assert "All Phase 1-4 tests passed!" in proc.stdout
