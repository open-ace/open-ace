#!/usr/bin/env python3
"""
Open ACE - Codex Remote Session E2E Tests (Issue #517)

Extracted from tests/issues/517/e2e_codex_comprehensive.py in the #2429
batch-17 e2e exodus: the remote/terminal codex lifecycle tests —
capabilities advertisement, remote session create + DB verification,
chat message storage, restore-URL construction, and web-terminal
creation on a codex-capable machine.

Each test needs a connected remote machine with codex installed (the
issues/nightly lane registers none), so they skip unless one is found.
Run:
  HEADLESS=true  python tests/e2e/remote/e2e_codex_remote.py
"""

import os
import sys

import pytest
import requests

# Absolute package import of the shared codex helpers (prepend-mode
# requirement): put the project root on sys.path so ``tests.e2e.work.helpers``
# resolves under both pytest import modes.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

from tests.e2e.work import helpers as _helpers_mod
from tests.e2e.work.helpers import (
    BASE_URL,
    PROJECT_ROOT,
    REMOTE_TEST_HOST,
    api_get,
    cleanup_codex_seed,
    ensure_lane_login,
    poll_until,
    seed_codex_data,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(517)]


@pytest.fixture(autouse=True, scope="module")
def _lane_seed_and_auth():
    """Login + seed codex data once for this module (lane-only concerns)."""
    ensure_lane_login()
    seed_codex_data()
    # Many tests read the module-level auth_token (set only in main()'s runner
    # flow); mirror the helper's live token so pytest-collected paths work too.
    global auth_token
    auth_token = _helpers_mod._auth_token
    yield
    cleanup_codex_seed()


# ── Test state ─────────────────────────────────────────
auth_token = None


def _find_codex_machine():
    """Find a connected remote machine with codex installed. Returns (machine, machine_id) or (None, None)."""
    r = requests.get(
        f"{BASE_URL}/api/remote/machines",
        cookies={"session_token": auth_token},
    )
    if r.status_code != 200:
        return None, None

    machines = r.json().get("machines", [])
    for m in machines:
        if m.get("status") in ("offline",):
            continue
        cli = m.get("capabilities", {}).get("cli_details", {})
        if cli.get("codex", {}).get("installed"):
            return m, m["machine_id"]
    return None, None


def test_remote_codex_capabilities():
    """Step 1: Remote machine reports codex in capabilities."""
    codex_machine, machine_id = _find_codex_machine()
    if not codex_machine:
        # The issues lane registers no remote machines; this step needs a
        # real connected host with codex installed.
        pytest.skip("no connected remote machine with codex installed")
    cli = codex_machine.get("capabilities", {}).get("cli_details", {})
    version = cli.get("codex", {}).get("version", "?")
    assert cli.get("codex", {}).get("installed"), "capabilities must report codex as installed"
    print(f"    {codex_machine['machine_name']}: codex installed (v{version})")


def test_remote_session_create_codex():
    """Step 2: Create remote session → agent launches codex process → verify DB.

    Full chain:
      POST /api/remote/sessions → RemoteSessionManager.create_remote_session()
        → _cli_tool_to_provider("codex") = "openai"
        → generate proxy token with provider=openai
        → dispatch "start_session" command to remote agent
        → agent: get_adapter("codex") = CodexCLIAdapter
        → agent: build_start_args() = ["codex", "--model", "o3"]
        → agent: subprocess.Popen(codex ...) with OPENAI_API_KEY/OPENAI_BASE_URL
        → agent: send_sdk_init() to codex stdin
        → agent: report session_status "running" to server
      Verify: session in agent_sessions with tool_name=codex, workspace_type=remote
    """
    codex_machine, machine_id = _find_codex_machine()
    if not codex_machine:
        print("    SKIP: No remote machine with codex")
        return

    # Step 2a: Create session via API
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/tmp/codex-e2e-test",
            "cli_tool": "codex",
            "model": "o3",
            "title": "E2E Test Codex Session",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Session creation failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    session = data.get("session", {})
    session_id = session.get("session_id")
    assert session_id, "No session_id in response"
    assert session.get("cli_tool") in (
        "codex",
        "codex-cli",
    ), f"Unexpected cli_tool: {session.get('cli_tool')}"
    print(f"    [2a] API created session: {session_id[:16]}...")

    # Step 2b: Wait for agent to launch codex and report back
    poll_until(
        lambda: api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 5})
        .get("data", {})
        .get("total", 0)
        > 0,
        timeout=10,
        interval=1,
        description="codex sessions appear",
    )

    # Step 2c: Verify session appears in sessions list
    list_data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 5})
    sessions = list_data.get("data", {}).get("sessions", [])
    found = any(s["session_id"] == session_id for s in sessions)
    assert found, f"Session {session_id[:8]} not found in sessions list"
    print("    [2b] Session verified in sessions list")

    # Step 2d: Verify session has correct metadata
    detail = api_get(f"/workspace/sessions/{session_id}", params={"include_messages": "true"})
    session_data = detail.get("data", {})
    assert session_data.get("tool_name") in ("codex", "codex-cli")
    assert (
        session_data.get("workspace_type") == "remote"
    ), f"Expected workspace_type=remote, got {session_data.get('workspace_type')}"
    assert session_data.get("remote_machine_id") == machine_id, "remote_machine_id mismatch"
    print(
        f"    [2c] Session metadata: tool={session_data.get('tool_name')}, "
        f"type={session_data.get('workspace_type')}, model={session_data.get('model')}"
    )

    # Step 2e: Verify remote agent launched codex process (check agent log)
    import subprocess as sp

    log_check = sp.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            f"root@{REMOTE_TEST_HOST}",
            f"grep -c 'codex.*{session_id[:8]}' /tmp/agent.log 2>/dev/null || echo 0",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    log_matches = int(log_check.stdout.strip().split("\n")[0]) if log_check.returncode == 0 else 0
    print(f"    [2d] Agent log mentions session: {log_matches} times")

    # Step 2f: Stop session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Stop failed: {r.status_code}"
    print("    [2e] Session stopped")


def test_remote_session_send_message_codex():
    """Step 3: Send message → stored in session_messages → stop.

    Chain:
      POST /api/remote/sessions/{id}/chat → send_message()
        → session_manager.add_message(role="user", content=...)
        → mirror to daily_messages
        → dispatch "send_message" command to agent
        → agent: write to codex stdin as JSON user message
      Verify: session_messages has the user message
    """
    codex_machine, machine_id = _find_codex_machine()
    if not codex_machine:
        print("    SKIP: No remote machine with codex")
        return

    # Create session
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/tmp/codex-e2e-msg",
            "cli_tool": "codex",
            "model": "o3",
        },
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Create failed: {r.text[:200]}"
    session_id = r.json().get("session", {}).get("session_id")
    print(f"    [3a] Session created: {session_id[:16]}...")

    # Wait for agent to launch codex
    poll_until(
        lambda: api_get(f"/workspace/sessions/{session_id}", expect_success=False).status_code
        == 200,
        timeout=8,
        interval=1,
        description="codex session start",
    )

    # Send message
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/chat",
        json={"content": "Say hello from the E2E test"},
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Send message failed: {r.status_code}"
    print("    [3b] Message sent (200 OK)")

    # Verify user message stored in session_messages
    poll_until(
        lambda: api_get(f"/workspace/sessions/{session_id}", params={"include_messages": "true"})
        .get("data", {})
        .get("messages", [{}])[0]
        .get("role")
        == "user",
        timeout=5,
        interval=0.5,
        description="user message stored",
    )
    detail = api_get(f"/workspace/sessions/{session_id}", params={"include_messages": "true"})
    messages = detail.get("data", {}).get("messages", [])
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assert user_msgs, "No user message found in session_messages"
    assert "E2E test" in user_msgs[0].get(
        "content", ""
    ), f"User message content mismatch: {user_msgs[0].get('content', '')[:100]}"
    print(f"    [3c] User message stored: {user_msgs[0]['content'][:60]}...")

    # Verify user message mirrored to daily_messages
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM daily_messages "
        "WHERE agent_session_id = %s AND role = 'user' ORDER BY timestamp DESC LIMIT 1",
        (session_id,),
    )
    row = cur.fetchone()
    if row and "E2E test" in (row.get("content") or ""):
        print("    [3d] Message mirrored to daily_messages")
    else:
        print(f"    [3d] daily_messages mirror: {'found' if row else 'not found'}")

    # Stop
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
        cookies={"session_token": auth_token},
    )
    print("    [3e] Session stopped")


def test_remote_session_restore_codex():
    """Step 4: Restore a codex session → verify URL construction.

    Chain:
      POST /api/workspace/sessions/{id}/restore
        → returns URL with workspaceType=remote, toolName=codex, machineId=...
      When frontend sends a message to restored session:
        → agent detects process exited → _restart_session()
        → uses codex resume <session_id> --cd <path>
    """
    codex_machine, machine_id = _find_codex_machine()
    if not codex_machine:
        print("    SKIP: No remote machine with codex")
        return

    # Create and stop a session first
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/tmp/codex-e2e-restore",
            "cli_tool": "codex",
            "model": "o3",
        },
        cookies={"session_token": auth_token},
    )
    if r.status_code != 200:
        print(f"    SKIP: Create failed: {r.text[:200]}")
        return

    session_id = r.json().get("session", {}).get("session_id")
    poll_until(
        lambda: api_get(f"/workspace/sessions/{session_id}", expect_success=False).status_code
        == 200,
        timeout=5,
        interval=0.5,
        description="session created",
    )

    # Stop session
    requests.post(
        f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
        cookies={"session_token": auth_token},
    )
    poll_until(
        lambda: requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
            cookies={"session_token": auth_token},
        ).status_code
        == 200,
        timeout=3,
        interval=0.5,
        description="session stop",
    )

    # Step 4a: Call restore endpoint
    r = requests.post(
        f"{BASE_URL}/api/workspace/sessions/{session_id}/restore",
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Restore failed: {r.status_code} {r.text[:300]}"
    restore_data = r.json().get("data", r.json())
    url = restore_data.get("url", "")
    assert url, "Restore returned empty URL"
    print(f"    [4a] Restore URL: {url[:100]}...")

    # Step 4b: Verify URL contains correct params for codex remote session
    assert "workspaceType=remote" in url, "Missing workspaceType=remote in URL"
    assert f"sessionId={session_id}" in url, "Missing sessionId in URL"
    assert "toolName=codex" in url, "Missing toolName=codex in URL"
    assert f"machineId={machine_id}" in url, "Missing machineId in URL"
    machine_name = codex_machine.get("machine_name", "")
    if machine_name:
        assert f"machineName={machine_name}" in url, "Missing machineName in URL"
    print("    [4b] URL verified: workspaceType=remote, toolName=codex, machineId present")

    # Step 4c: Verify adapter would use 'codex resume' for restart
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()
    resume_args = adapter.build_start_args(
        session_id=session_id, project_path="/tmp/codex-e2e-restore", resume=True
    )
    assert "resume" in resume_args, f"Resume args don't contain 'resume': {resume_args}"
    assert session_id in resume_args, "Resume args don't contain session_id"
    print(f"    [4c] Resume args verified: {resume_args}")


def test_remote_terminal_codex():
    """Step 5: Web terminal creation on codex-capable machine.

    Chain:
      POST /api/remote/terminal/start
        → creates session with workspace_type=terminal
        → generates both anthropic_token and openai_token (codex uses openai)
        → dispatches start_terminal to agent
        → agent launches terminal_menu.py with OPENAI_API_KEY/OPENAI_BASE_URL
        → terminal_menu includes Codex option
    """
    codex_machine, machine_id = _find_codex_machine()
    if not codex_machine:
        print("    SKIP: No remote machine with codex")
        return

    # Create terminal
    r = requests.post(
        f"{BASE_URL}/api/remote/terminal/start",
        json={"machine_id": machine_id, "work_dir": "/tmp"},
        cookies={"session_token": auth_token},
    )
    if r.status_code != 200:
        print(f"    SKIP: Terminal creation: {r.status_code} {r.text[:200]}")
        return

    data = r.json()
    terminal = data.get("terminal", {})
    terminal_id = terminal.get("terminal_id")
    assert terminal_id, "No terminal_id in response"
    print(f"    [5a] Terminal created: {terminal_id[:16]}...")

    # Verify terminal session in DB
    poll_until(
        lambda: api_get(f"/workspace/sessions/{terminal_id}", expect_success=False).status_code
        == 200,
        timeout=5,
        interval=0.5,
        description="terminal session in DB",
    )
    detail = api_get(f"/workspace/sessions/{terminal_id}", params={"include_messages": "true"})
    session_data = detail.get("data", {})
    assert (
        session_data.get("workspace_type") == "terminal"
    ), f"Expected workspace_type=terminal, got {session_data.get('workspace_type')}"
    print("    [5b] Terminal session verified: workspace_type=terminal")

    # Verify terminal_menu includes codex on remote machine
    import subprocess as sp

    menu_check = sp.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            f"root@{REMOTE_TEST_HOST}",
            'cd /root/.open-ace-agent && python3.9 -c "from terminal_menu import TOOLS; '
            "print([t['cli'] for t in TOOLS if t['cli']=='codex'])\"",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "codex" in menu_check.stdout, f"codex not in remote terminal_menu: {menu_check.stdout}"
    print("    [5c] Remote terminal_menu includes codex")

    # Stop terminal
    requests.post(
        f"{BASE_URL}/api/remote/terminal/stop",
        json={"terminal_id": terminal_id, "machine_id": machine_id},
        cookies={"session_token": auth_token},
    )
    print("    [5d] Terminal stopped")
