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
  HEADLESS=true  python tests/e2e/work/e2e_codex_comprehensive.py
  HEADLESS=false python tests/e2e/work/e2e_codex_comprehensive.py

#2429 exodus note: the five remote/terminal codex lifecycle tests moved to
tests/e2e/remote/e2e_codex_remote.py (remote area); this file keeps the
data-layer, session-sync, quota, restore and API-endpoint tests.
"""

import json
import os
import sys
import time
import uuid

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
    FETCH_HOSTNAME,
    PROJECT_ROOT,
    TEST_PASS,
    TEST_USER,
    TestResults,
    api_get,
    api_login,
    api_post,
    cleanup_codex_seed,
    ensure_lane_login,
    poll_until,
    print_results,
    run_test,
    seed_codex_data,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(517)]

# ── Configuration ──────────────────────────────────────
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-codex-comprehensive")


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
results = TestResults()


# ═══════════════════════════════════════════════════════
# SECTION 1: Data Layer
# ═══════════════════════════════════════════════════════


def test_fetch_codex_data():
    """fetch_codex.py processes sessions with tokens."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(PROJECT_ROOT, "scripts", "fetch_codex.py"),
            "--days",
            "999",
            # Tag side-effect rows so cleanup can remove exactly them and
            # never touch real-host data on a reuse server.
            "--hostname",
            FETCH_HOSTNAME,
        ],
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
    assert (row["total"] or 0) > 0, "No codex tokens in daily_usage"
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


def test_api_key_proxy_codex_lookup():
    """get_cli_settings_for_tool handles codex tool name normalization."""
    import os

    # Ensure encryption key is set for APIKeyProxyService
    if not os.environ.get("OPENACE_ENCRYPTION_KEY") and not os.environ.get("SECRET_KEY"):
        os.environ["OPENACE_ENCRYPTION_KEY"] = "test-encryption-key-for-e2e-testing-only"

    from app.modules.workspace.api_key_proxy import APIKeyProxyService

    service = APIKeyProxyService()
    result = service.get_cli_settings_for_tool(tenant_id=1, tool_name="codex")
    print(f"    codex settings lookup: {result}")
    # Documented contract: settings dict, or None when no key advertises codex.
    assert result is None or isinstance(result, dict), f"unexpected lookup result: {result!r}"


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
        assert can_use is not None, f"quota check response missing can_use: {data}"
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
        cookies={"session_token": _helpers_mod._auth_token},
    )
    assert r.status_code == 200, f"Usage API failed: {r.status_code}"
    data = r.json()
    usage = data if isinstance(data, list) else data.get("data", [])
    assert usage, "No codex usage data"
    total = sum(u.get("tokens_used", 0) for u in usage if isinstance(u, dict))
    print(f"    {len(usage)} days, {total:,} tokens")


def test_api_tools_list():
    """Tools list query includes codex.

    /api/tools is served from a ttl=300 in-process cache; a sibling browser
    test can prime it with the empty pre-seed list, so assert the underlying
    repository query (what the endpoint computes) instead of the cached HTTP
    response — order-independent within the shard.
    """
    from app.repositories.usage_repo import UsageRepository

    tools = UsageRepository().get_all_tools(tenant_id=1)
    assert "codex" in tools, f"codex not in tools: {tools}"
    print(f"    Tools: {tools}")


def test_api_codex_alias():
    """codex-cli alias resolves to codex sessions."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex-cli", "limit": 3})
    # The alias should work even if 0 sessions match
    assert "data" in data, f"unexpected sessions response shape: {list(data)}"
    print(f"    codex-cli alias: {data.get('data', {}).get('total', 0)} sessions")


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
    # (extracted to tests/unit/test_codex_adapter_inprocess_517.py in batch 16)

    # ── Phase 3: Session Sync (no server required) ──
    print("\n── Phase 3: Session Sync ──")
    run_test("CodexSession parser", test_session_sync_codex_parser)
    run_test("CodexSession sync payload", test_session_sync_payload)
    run_test("Session sync scan dirs include codex", test_session_sync_scan_dirs)

    # ── Phase 4: API key proxy (server required) ──
    # (the remote-session lifecycle moved to tests/e2e/remote/e2e_codex_remote.py)
    print("\n── Phase 4: API Key Proxy ──")

    # Login for API tests
    try:
        auth_token = api_login()
        _admin_token = api_login("admin", TEST_PASS)
        print("  Logged in successfully")
    except (AssertionError, requests.exceptions.RequestException) as e:
        print(f"  SKIP: Login failed: {e}")
        print("  Skipping API-dependent tests")
        print_results(results)
        return

    # Monkey-patch global auth_token for api_get/api_post
    globals()["auth_token"] = auth_token

    run_test("API key proxy codex lookup", test_api_key_proxy_codex_lookup)

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

    # ── Phase 8: Backend Modules ──
    # (extracted to tests/unit/test_codex_adapter_inprocess_517.py in batch 16)

    if not print_results(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
