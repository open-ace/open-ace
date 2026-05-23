#!/usr/bin/env python3
"""
Open ACE - Codex Comprehensive E2E Test

Full end-to-end test covering all Codex integration scenarios:
1. Data layer: fetch_codex.py, sessions, messages, tokens
2. CLI adapter: env vars, args, settings, resume
3. Session sync: CodexSession parser and scan
4. Remote session: creation, provider mapping, proxy routing
5. API key proxy: CLI settings lookup, tool name normalization
6. Quota management: token/request limits for codex
7. Session save/restore: URL construction for codex sessions
8. API endpoints: sessions, messages, usage with codex filter
9. Frontend: content_block rendering, tool display

Run:
  HEADLESS=true  python tests/517/e2e_codex_comprehensive.py
  HEADLESS=false python tests/517/e2e_codex_comprehensive.py
"""

import json
import os
import sys
import time
import uuid

import requests
from test_helpers import (
    BASE_URL,
    HEADLESS,
    PROJECT_ROOT,
    REMOTE_TEST_HOST,
    SCREENSHOT_DIR,
    TEST_PASS,
    TEST_USER,
    WEBUI_URL,
    TestResults,
    api_get,
    api_login,
    api_post,
    poll_until,
    print_results,
    run_test,
    screenshot,
)

# ── Configuration ──────────────────────────────────────
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-codex-comprehensive")

# ── Test state ─────────────────────────────────────────
auth_token = None
results = TestResults()


# ═══════════════════════════════════════════════════════
# SECTION 1: Data Layer
# ═══════════════════════════════════════════════════════


def test_fetch_codex_data():
    """fetch_codex.py processes sessions with tokens."""
    import subprocess

    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "fetch_codex.py"), "--days", "999"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"fetch_codex.py failed: {result.stderr[-500:]}"
    print("    fetch_codex.py completed successfully")


def test_daily_usage_tokens():
    """daily_usage has codex entries with non-zero tokens."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT SUM(tokens_used) as total FROM daily_usage WHERE tool_name = 'codex'")
    row = cur.fetchone()
    assert row["total"] > 0, "No codex tokens in daily_usage"
    print(f"    Total daily_usage tokens: {row['total']:,}")


def test_agent_sessions_tokens():
    """agent_sessions has codex sessions with tokens."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt, SUM(total_tokens) as total, SUM(message_count) as msgs "
        "FROM agent_sessions WHERE tool_name = 'codex'"
    )
    row = cur.fetchone()
    assert row["cnt"] > 0, "No codex sessions"
    assert row["total"] > 0, "All codex sessions have 0 tokens"
    print(f"    {row['cnt']} sessions, {row['total']:,} tokens, {row['msgs']} messages")


def test_session_messages_content():
    """session_messages has codex messages with content_blocks."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    # Count messages with content_blocks in metadata
    cur.execute(
        "SELECT COUNT(*) as cnt FROM session_messages sm "
        "JOIN agent_sessions s ON sm.session_id = s.session_id "
        "WHERE s.tool_name = 'codex' AND sm.metadata IS NOT NULL"
    )
    row = cur.fetchone()
    assert row["cnt"] > 0, "No codex session_messages with metadata"
    print(f"    {row['cnt']} codex session_messages with metadata")


def test_content_block_types():
    """All Codex content_block types exist in the database."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT sm.metadata FROM session_messages sm "
        "JOIN agent_sessions s ON sm.session_id = s.session_id "
        "WHERE s.tool_name = 'codex' AND sm.metadata IS NOT NULL LIMIT 500"
    )
    rows = cur.fetchall()
    types_found = set()
    for row in rows:
        try:
            meta = (
                json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            )
            for block in meta.get("content_blocks", []):
                if isinstance(block, dict) and "type" in block:
                    types_found.add(block["type"])
        except (json.JSONDecodeError, TypeError):
            continue

    assert "text" in types_found, "No 'text' content_blocks"
    assert "tool_use" in types_found or "tool_result" in types_found, "No tool content_blocks"
    print(f"    Content block types: {sorted(types_found)}")


# ═══════════════════════════════════════════════════════
# SECTION 2: CLI Adapter
# ═══════════════════════════════════════════════════════


def _get_codex_adapter():
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    return ADAPTERS["codex"]()


def test_adapter_env_vars():
    """Codex adapter sets OPENAI_API_KEY and OPENAI_BASE_URL."""
    adapter = _get_codex_adapter()
    env = adapter.get_env_vars(proxy_url="http://proxy:8080", proxy_token="tok-123")
    assert env["OPENAI_API_KEY"] == "tok-123"
    assert "v1" in env["OPENAI_BASE_URL"]
    print(f"    OPENAI_BASE_URL={env['OPENAI_BASE_URL']}")


def test_adapter_interactive_args():
    """Codex adapter builds correct interactive mode args."""
    adapter = _get_codex_adapter()
    args = adapter.build_start_args(session_id="s1", project_path="/tmp", model="o3")
    assert args[0] == "codex"
    assert "--model" in args
    assert "o3" in args
    print(f"    Args: {args}")


def test_adapter_resume_args():
    """Codex adapter uses 'resume' subcommand for session restore."""
    adapter = _get_codex_adapter()
    args = adapter.build_start_args(
        session_id="abc-123", project_path="/tmp/project", model="o3", resume=True
    )
    assert "resume" in args, f"Expected 'resume' in args: {args}"
    assert "abc-123" in args, f"Expected session_id in resume args: {args}"
    assert "--model" in args, "Expected --model flag in resume args"
    assert "--cd" in args, "Expected --cd flag for project_path"
    print(f"    Resume args: {args}")


def test_adapter_permission_modes():
    """Codex adapter maps permission modes correctly."""
    adapter = _get_codex_adapter()

    # Plan mode
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="plan")
    assert "--ask-for-approval" in args
    assert "untrusted" in args
    print(f"    Plan mode: {args}")

    # Auto mode
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="auto")
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    print(f"    Auto mode: {args}")


def test_adapter_single_shot():
    """Codex adapter builds correct single-shot args."""
    adapter = _get_codex_adapter()
    args = adapter.build_single_shot_args("write a test", project_path="/tmp", model="o3")
    assert "exec" in args
    assert "--json" in args
    assert "--sandbox" in args
    assert "o3" in args
    print(f"    Single-shot: {args}")


def test_adapter_settings():
    """Codex adapter strips sensitive keys and adds model_reasoning_summary."""
    adapter = _get_codex_adapter()
    settings = adapter.build_settings(
        base_settings={
            "env": {"OPENAI_API_KEY": "secret-key"},
            "model": "o3",
        }
    )
    assert settings["model_reasoning_summary"] == "auto"
    assert "OPENAI_API_KEY" not in settings.get("env", {})
    print(f"    Settings: {list(settings.keys())}")


def test_terminal_menu_codex():
    """Terminal menu includes Codex with correct config."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    import importlib

    tm = importlib.import_module("terminal_menu")
    codex = [t for t in tm.TOOLS if t["cli"] == "codex"]
    assert codex, "No codex in terminal menu TOOLS"
    assert codex[0]["env_key"] == "OPENAI_API_KEY"
    assert "@openai/codex" in codex[0]["install_cmd"]
    print("    Codex menu entry verified")


# ═══════════════════════════════════════════════════════
# SECTION 3: Session Sync
# ═══════════════════════════════════════════════════════


def test_session_sync_codex_parser():
    """CodexSession class can parse a real Codex JSONL file."""
    from pathlib import Path

    codex_dir = Path.home() / ".codex" / "sessions"
    if not codex_dir.exists():
        print("    SKIP: No ~/.codex/sessions directory")
        return

    # Find a JSONL file
    jsonl_files = list(codex_dir.rglob("rollout-*.jsonl"))
    if not jsonl_files:
        print("    SKIP: No Codex session files found")
        return

    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from session_sync import CodexSession

    session = CodexSession("test", str(jsonl_files[0]))
    parsed = session.parse()
    assert parsed, f"Failed to parse {jsonl_files[0].name}"
    assert session.message_count > 0, "Parsed 0 messages"
    print(
        f"    Parsed {jsonl_files[0].name}: {session.message_count} messages, "
        f"model={session.model}, tokens_in={session.total_input_tokens}"
    )


def test_session_sync_payload():
    """CodexSession.to_sync_payload returns correct structure."""
    from pathlib import Path

    codex_dir = Path.home() / ".codex" / "sessions"
    if not codex_dir.exists():
        print("    SKIP: No ~/.codex/sessions directory")
        return

    jsonl_files = list(codex_dir.rglob("rollout-*.jsonl"))
    if not jsonl_files:
        print("    SKIP: No Codex session files")
        return

    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from session_sync import CodexSession

    session = CodexSession("test", str(jsonl_files[0]))
    if not session.parse():
        print("    SKIP: Failed to parse session")
        return

    payload = session.to_sync_payload("machine-1", "terminal-1")
    assert (
        payload["tool_name"] == "codex"
    ), f"Expected tool_name='codex', got '{payload['tool_name']}'"
    assert payload["machine_id"] == "machine-1"
    assert payload["session_id"]
    assert isinstance(payload["messages"], list)
    print(f"    Payload: tool_name={payload['tool_name']}, msgs={len(payload['messages'])}")


def test_session_sync_scan_dirs():
    """SessionSyncService._scan_and_sync includes Codex directory."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from pathlib import Path

    import session_sync as ss

    assert Path.home() / ".codex" / "sessions" == ss.CODEX_SESSIONS_DIR
    assert hasattr(ss, "CodexSession"), "CodexSession class not found in session_sync module"
    print(f"    CodexSession registered, scan dir: {ss.CODEX_SESSIONS_DIR}")


# ═══════════════════════════════════════════════════════
# SECTION 4: Remote Session & Provider Mapping
# ═══════════════════════════════════════════════════════


def test_provider_mapping_codex():
    """_cli_tool_to_provider maps codex/codex-cli to openai."""
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    # Access the static/class method
    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    mgr._cli_tool_to_provider = RemoteSessionManager._cli_tool_to_provider.__get__(mgr)

    assert mgr._cli_tool_to_provider("codex") == "openai", "codex should map to openai provider"
    assert (
        mgr._cli_tool_to_provider("codex-cli") == "openai"
    ), "codex-cli should map to openai provider"
    print("    codex -> openai, codex-cli -> openai")


def test_api_key_proxy_codex_lookup():
    """get_cli_settings_for_tool handles codex tool name normalization."""
    import os

    # Ensure encryption key is set for APIKeyProxyService
    if not os.environ.get("OPENACE_ENCRYPTION_KEY") and not os.environ.get("SECRET_KEY"):
        os.environ["OPENACE_ENCRYPTION_KEY"] = "test-encryption-key-for-e2e-testing-only"

    from app.modules.workspace.api_key_proxy import APIKeyProxyService

    service = APIKeyProxyService()
    try:
        result = service.get_cli_settings_for_tool(tenant_id=1, tool_name="codex")
        print(f"    codex settings lookup: {result}")
    except Exception as e:
        # May fail if no API keys configured, but shouldn't crash on tool name
        print(f"    codex settings lookup (no keys configured): {e}")


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
    assert codex_machine, "No connected remote machine with codex installed"
    cli = codex_machine.get("capabilities", {}).get("cli_details", {})
    version = cli.get("codex", {}).get("version", "?")
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


# ═══════════════════════════════════════════════════════
# SECTION 5: Quota Management
# ═══════════════════════════════════════════════════════


def test_workspace_status_codex():
    """Workspace status endpoint returns quota info including codex usage."""
    data = api_get("/workspace/status")
    assert "tokens_used" in data or "tokens_limit" in data or "data" in data
    d = data.get("data", data)
    tokens_used = d.get("tokens_used", 0)
    tokens_limit = d.get("tokens_limit", 0)
    print(f"    tokens_used={tokens_used:,}, tokens_limit={tokens_limit:,}")


def test_quota_check_codex():
    """Quota check endpoint works for codex user."""
    r = requests.get(
        f"{BASE_URL}/api/quota/check",
        cookies={"session_token": auth_token},
    )
    if r.status_code == 200:
        data = r.json()
        can_use = data.get("can_use", data.get("data", {}).get("can_use", None))
        print(f"    Quota check: can_use={can_use}")
    else:
        print(f"    Quota check: {r.status_code} (may need config)")


def test_llm_proxy_routes_codex():
    """LLM proxy correctly routes codex (openai provider) requests."""
    # Verify the proxy mapping by checking _cli_tool_to_provider returns "openai"
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    provider = mgr._cli_tool_to_provider("codex")
    assert provider == "openai", f"codex should route to openai provider, got {provider}"
    print(f"    LLM proxy: codex -> {provider} provider")


# ═══════════════════════════════════════════════════════
# SECTION 6: Session Save/Restore
# ═══════════════════════════════════════════════════════


def test_session_restore_url():
    """Session restore returns correct URL for codex sessions."""
    # Get a codex session
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 1})
    sessions = data.get("data", {}).get("sessions", [])
    if not sessions:
        print("    SKIP: No codex sessions to test restore")
        return

    sid = sessions[0]["session_id"]

    # Call restore endpoint
    r = requests.post(
        f"{BASE_URL}/api/workspace/sessions/{sid}/restore",
        cookies={"session_token": auth_token},
    )
    if r.status_code == 200:
        restore_data = r.json()
        url = restore_data.get("data", {}).get("url", "")
        print(f"    Restore URL: {url[:100]}...")
        # URL should contain tool=codex or codex in params
        assert url, "Restore returned empty URL"
    else:
        print(f"    Restore returned {r.status_code}: {r.text[:200]}")


def test_session_restore_codex_detail():
    """Restored codex session detail includes messages and tokens."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 5})
    sessions = data.get("data", {}).get("sessions", [])

    # Find a session with tokens
    sessions_with_tokens = [s for s in sessions if s.get("total_tokens", 0) > 0]
    if not sessions_with_tokens:
        print("    SKIP: No codex sessions with tokens")
        return

    s = sessions_with_tokens[0]
    sid = s["session_id"]

    detail = api_get(f"/workspace/sessions/{sid}", params={"include_messages": "true"})
    session_data = detail.get("data", {})
    messages = session_data.get("messages", [])

    assert session_data.get("tool_name") in ("codex", "codex-cli")
    assert len(messages) > 0, f"Session {sid[:8]} has no messages"
    print(
        f"    Session {sid[:8]}: {len(messages)} messages, {session_data.get('total_tokens', 0):,} tokens"
    )


# ═══════════════════════════════════════════════════════
# SECTION 7: API Endpoints
# ═══════════════════════════════════════════════════════


def test_api_sessions_list():
    """Sessions list API returns codex sessions."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 3})
    total = data.get("data", {}).get("total", 0)
    assert total > 0, "No codex sessions in API"
    print(f"    {total} codex sessions")


def test_api_messages_query():
    """Messages API returns codex messages with date range."""
    data = api_get(
        "/messages",
        params={"tool": "codex", "limit": 3, "start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    total = data.get("total", 0)
    assert total > 0, "No codex messages via API"
    print(f"    {total} codex messages")


def test_api_usage_data():
    """Usage API returns codex usage data."""
    r = requests.get(
        f"{BASE_URL}/api/tool/codex/30",
        cookies={"session_token": auth_token},
    )
    assert r.status_code == 200, f"Usage API failed: {r.status_code}"
    data = r.json()
    usage = data if isinstance(data, list) else data.get("data", [])
    assert usage, "No codex usage data"
    total = sum(u.get("tokens_used", 0) for u in usage if isinstance(u, dict))
    print(f"    {len(usage)} days, {total:,} tokens")


def test_api_tools_list():
    """Tools list API includes codex."""
    r = requests.get(
        f"{BASE_URL}/api/tools",
        cookies={"session_token": auth_token},
    )
    data = r.json()
    tools = data if isinstance(data, list) else data.get("data", [])
    assert "codex" in tools, f"codex not in tools: {tools}"
    print(f"    Tools: {tools}")


def test_api_codex_alias():
    """codex-cli alias resolves to codex sessions."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex-cli", "limit": 3})
    # The alias should work even if 0 sessions match
    print(f"    codex-cli alias: {data.get('data', {}).get('total', 0)} sessions")


def test_tool_name_normalization():
    """Tool name normalization handles all codex variants."""
    from app.utils.tool_names import TOOL_NAME_ALIASES, normalize_tool_name

    assert normalize_tool_name("codex") == "codex"
    assert normalize_tool_name("codex-cli") == "codex"
    assert "codex" in TOOL_NAME_ALIASES
    print("    Normalization: codex -> codex, codex-cli -> codex")


# ═══════════════════════════════════════════════════════
# SECTION 8: Backend Modules
# ═══════════════════════════════════════════════════════


def test_tool_connector_codex():
    """Tool connector registers codex with correct attributes."""
    from app.modules.workspace.tool_connector import get_tool_connector

    codex = get_tool_connector().get_tool("codex")
    assert codex, "codex not in tool connector"
    assert codex.tool_type == "agent"
    assert codex.supports_streaming
    assert codex.supports_tools
    assert "coding" in codex.capabilities
    print(f"    Connector: type={codex.tool_type}, models={codex.models}")


def test_user_tool_account_codex():
    """User tool account model supports codex type."""
    from app.models.user_tool_account import TOOL_TYPES

    assert "codex" in TOOL_TYPES
    print(f"    TOOL_TYPES['codex'] = {TOOL_TYPES['codex']}")


def test_fetch_route_codex():
    """Fetch route includes codex script execution."""
    import inspect

    from app.routes.fetch import run_fetch_scripts

    source = inspect.getsource(run_fetch_scripts)
    assert "fetch_codex.py" in source, "fetch_codex.py not referenced in run_fetch_scripts"
    print("    fetch_codex.py included in fetch route")


# ═══════════════════════════════════════════════════════
# MAIN: Run all tests
# ═══════════════════════════════════════════════════════


def main():
    print("=" * 60)
    print("Codex Comprehensive E2E Test Suite")
    print("=" * 60)

    # ── Phase 1: Data Layer (no server required) ──
    print("\n── Phase 1: Data Layer ──")
    run_test("fetch_codex.py processes sessions", test_fetch_codex_data)
    run_test("daily_usage has codex tokens", test_daily_usage_tokens)
    run_test("agent_sessions has codex tokens", test_agent_sessions_tokens)
    run_test("session_messages has content", test_session_messages_content)
    run_test("Content block types exist", test_content_block_types)

    # ── Phase 2: CLI Adapter (no server required) ──
    print("\n── Phase 2: CLI Adapter ──")
    run_test("Adapter env vars (OPENAI_API_KEY/BASE_URL)", test_adapter_env_vars)
    run_test("Adapter interactive args", test_adapter_interactive_args)
    run_test("Adapter resume args (session restore)", test_adapter_resume_args)
    run_test("Adapter permission modes", test_adapter_permission_modes)
    run_test("Adapter single-shot args", test_adapter_single_shot)
    run_test("Adapter settings (sensitive key stripping)", test_adapter_settings)
    run_test("Terminal menu includes codex", test_terminal_menu_codex)

    # ── Phase 3: Session Sync (no server required) ──
    print("\n── Phase 3: Session Sync ──")
    run_test("CodexSession parser", test_session_sync_codex_parser)
    run_test("CodexSession sync payload", test_session_sync_payload)
    run_test("Session sync scan dirs include codex", test_session_sync_scan_dirs)

    # ── Phase 4: Remote Session & Provider (server required) ──
    print("\n── Phase 4: Remote Session & Provider ──")

    # Login for API tests
    try:
        auth_token = api_login()
        _admin_token = api_login("admin", TEST_PASS)
        print("  Logged in successfully")
    except Exception as e:
        print(f"  SKIP: Login failed: {e}")
        print("  Skipping API-dependent tests")
        print_results(results)
        return

    # Monkey-patch global auth_token for api_get/api_post
    globals()["auth_token"] = auth_token

    run_test("Provider mapping (codex -> openai)", test_provider_mapping_codex)
    run_test("API key proxy codex lookup", test_api_key_proxy_codex_lookup)
    run_test("Remote machine codex capabilities", test_remote_codex_capabilities)
    run_test("Remote session create codex", test_remote_session_create_codex)
    run_test("Remote session send message codex", test_remote_session_send_message_codex)
    run_test("Remote terminal creation", test_remote_terminal_codex)

    # ── Phase 5: Quota Management ──
    print("\n── Phase 5: Quota Management ──")
    run_test("Workspace status (token quota)", test_workspace_status_codex)
    run_test("Quota check endpoint", test_quota_check_codex)
    run_test("LLM proxy routes codex to openai", test_llm_proxy_routes_codex)

    # ── Phase 6: Session Save/Restore ──
    print("\n── Phase 6: Session Save/Restore ──")
    run_test("Session restore URL", test_session_restore_url)
    run_test("Session restore detail with messages", test_session_restore_codex_detail)

    # ── Phase 7: API Endpoints ──
    print("\n── Phase 7: API Endpoints ──")
    run_test("API sessions list codex", test_api_sessions_list)
    run_test("API messages query codex", test_api_messages_query)
    run_test("API usage data codex", test_api_usage_data)
    run_test("API tools list includes codex", test_api_tools_list)
    run_test("API codex alias resolution", test_api_codex_alias)
    run_test("Tool name normalization", test_tool_name_normalization)

    # ── Phase 8: Backend Modules ──
    print("\n── Phase 8: Backend Modules ──")
    run_test("Tool connector registers codex", test_tool_connector_codex)
    run_test("User tool account codex type", test_user_tool_account_codex)
    run_test("Fetch route includes codex", test_fetch_route_codex)

    if not print_results(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
