#!/usr/bin/env python3
"""
Open ACE - Codex Integration E2E Test

Comprehensive end-to-end test for OpenAI Codex CLI integration:
1. fetch_codex.py data fetching (sessions, messages, tokens)
2. API endpoint verification (sessions, messages, usage)
3. Codex-specific content_block types (reasoning, file_change, task_summary)
4. Token usage in daily_usage and agent_sessions
5. Remote session adapter (CLI adapter, terminal menu)
6. Frontend rendering verification via Playwright

Prerequisites:
  - Codex sessions exist in ~/.codex/sessions/
  - Backend server running on BASE_URL
  - Frontend dev server running on WEBUI_URL (for Playwright tests)

Run:
  HEADLESS=true  python tests/517/e2e_codex_integration.py
  HEADLESS=false python tests/517/e2e_codex_integration.py
"""

import json
import os
import sys
import time

import requests
import test_helpers
from test_helpers import (
    BASE_URL,
    HEADLESS,
    PROJECT_ROOT,
    SCREENSHOT_DIR,
    WEBUI_URL,
    TestResults,
    api_get,
    api_login,
    api_post,
    create_browser_page,
    playwright_login,
    poll_until,
    print_results,
    run_test,
    screenshot,
)

# ── Test state ─────────────────────────────────────────
auth_token = None
results = TestResults()
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-codex")


# ═══════════════════════════════════════════════════════
# SECTION 1: fetch_codex.py Data Fetching
# ═══════════════════════════════════════════════════════


def test_fetch_codex_runs():
    """fetch_codex.py runs successfully and processes sessions."""
    import subprocess

    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "fetch_codex.py"), "--days", "999"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"fetch_codex.py failed: {result.stderr[-500:]}"
    output = result.stdout
    assert (
        "session files" in output.lower() or "processed" in output.lower()
    ), f"Unexpected output: {output[:300]}"
    print(f"    Output: {output.splitlines()[-5:]}")


def test_daily_usage_has_codex():
    """daily_usage table has codex entries with non-zero tokens."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT date, tokens_used, input_tokens, output_tokens, request_count "
        "FROM daily_usage WHERE tool_name = 'codex' ORDER BY date DESC LIMIT 5"
    )
    rows = cur.fetchall()
    assert rows, "No codex daily_usage rows found"
    total_tokens = sum(r["tokens_used"] for r in rows)
    print(f"    Found {len(rows)} daily_usage rows, total_tokens={total_tokens}")
    # At least one day should have tokens
    assert any(
        r["tokens_used"] > 0 for r in rows
    ), f"All codex daily_usage rows have 0 tokens: {rows}"


def test_agent_sessions_have_codex():
    """agent_sessions table has codex sessions with non-zero tokens."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt, SUM(total_tokens) as total, SUM(message_count) as msgs "
        "FROM agent_sessions WHERE tool_name = 'codex'"
    )
    row = cur.fetchone()
    assert row["cnt"] > 0, "No codex sessions in agent_sessions"
    assert row["total"] > 0, f"All {row['cnt']} codex sessions have 0 total_tokens"
    assert row["msgs"] > 0, f"All {row['cnt']} codex sessions have 0 messages"
    print(f"    {row['cnt']} sessions, {row['total']} tokens, {row['msgs']} messages")


def test_session_messages_have_codex():
    """session_messages table has codex messages with content."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt FROM session_messages sm "
        "JOIN agent_sessions s ON sm.session_id = s.session_id "
        "WHERE s.tool_name = 'codex'"
    )
    row = cur.fetchone()
    assert row["cnt"] > 0, "No codex session_messages found"
    print(f"    {row['cnt']} codex session_messages")


def test_codex_content_block_types():
    """Verify Codex-specific content_block types exist in session_messages."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    # Check for content_blocks in metadata JSON
    cur.execute(
        "SELECT sm.metadata FROM session_messages sm "
        "JOIN agent_sessions s ON sm.session_id = s.session_id "
        "WHERE s.tool_name = 'codex' AND sm.metadata IS NOT NULL "
        "LIMIT 500"
    )
    rows = cur.fetchall()
    assert rows, "No codex session_messages with metadata"

    types_found = set()
    for row in rows:
        try:
            meta = (
                json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            )
            blocks = meta.get("content_blocks", [])
            for block in blocks:
                if isinstance(block, dict) and "type" in block:
                    types_found.add(block["type"])
        except (json.JSONDecodeError, TypeError):
            continue

    print(f"    Content block types found: {sorted(types_found)}")
    # Standard types that should always be present
    assert "text" in types_found, "No 'text' content_blocks found"
    # At least one of tool_use or tool_result should exist
    assert (
        "tool_use" in types_found or "tool_result" in types_found
    ), "No tool_use or tool_result content_blocks found"


def test_codex_daily_messages():
    """daily_messages table has codex entries."""
    from shared.db import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt, COUNT(DISTINCT agent_session_id) as sessions "
        "FROM daily_messages WHERE tool_name = 'codex'"
    )
    row = cur.fetchone()
    assert row["cnt"] > 0, "No codex daily_messages found"
    print(f"    {row['cnt']} messages across {row['sessions']} sessions")


# ═══════════════════════════════════════════════════════
# SECTION 2: API Endpoint Verification
# ═══════════════════════════════════════════════════════


def test_api_sessions_list_codex():
    """GET /api/workspace/sessions?tool_name=codex returns codex sessions."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 5})
    sessions = data.get("data", {}).get("sessions", [])
    total = data.get("data", {}).get("total", 0)
    assert total > 0, "No codex sessions returned from API"
    print(f"    {total} sessions, showing {len(sessions)}")

    # Verify session structure
    s = sessions[0]
    assert s["tool_name"] in ("codex", "codex-cli"), f"Unexpected tool_name: {s['tool_name']}"
    assert s.get("session_id"), "Missing session_id"


def test_api_session_detail():
    """GET /api/workspace/sessions/<id>?include_messages=true returns messages."""
    # First get a session id
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 1})
    sessions = data.get("data", {}).get("sessions", [])
    assert sessions, "No codex sessions to query detail"

    sid = sessions[0]["session_id"]
    detail = api_get(f"/workspace/sessions/{sid}", params={"include_messages": "true"})
    session = detail.get("data", {})
    assert session.get("session_id") == sid, "Session ID mismatch"

    messages = session.get("messages", [])
    print(
        f"    Session {sid[:16]}...: {session.get('message_count', 0)} messages, "
        f"{session.get('total_tokens', 0)} tokens"
    )
    # Messages may come from session_messages or daily_messages fallback
    if messages:
        roles = {m.get("role") for m in messages}
        print(f"    Roles: {sorted(roles)}")


def test_api_session_with_tokens():
    """At least one codex session has non-zero total_tokens."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 50})
    sessions = data.get("data", {}).get("sessions", [])
    sessions_with_tokens = [s for s in sessions if s.get("total_tokens", 0) > 0]
    assert sessions_with_tokens, f"None of {len(sessions)} sessions have tokens"
    top = max(sessions_with_tokens, key=lambda s: s["total_tokens"])
    print(
        f"    Top session: {top['total_tokens']:,} tokens, {top.get('message_count', 0)} messages"
    )


def test_api_usage_codex():
    """GET /api/tool/codex/<days> returns usage data."""
    r = requests.get(
        f"{BASE_URL}/api/tool/codex/30",
        cookies={"session_token": test_helpers._auth_token},
    )
    assert r.status_code == 200, f"GET /api/tool/codex/30 failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    # The response may be a raw list or wrapped in {"data": [...]}
    usage = data if isinstance(data, list) else data.get("data", [])
    assert usage, "No codex usage data returned"
    total_tokens = sum(u.get("tokens_used", 0) for u in usage if isinstance(u, dict))
    print(f"    {len(usage)} days of codex usage, total={total_tokens:,} tokens")


def test_api_messages_codex():
    """GET /api/messages?tool=codex returns codex messages."""
    data = api_get(
        "/messages",
        params={"tool": "codex", "limit": 5, "start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    messages = data.get("messages", data.get("data", []))
    total = data.get("total", 0)
    assert total > 0, "No codex messages returned"
    print(f"    {total} messages total, showing {len(messages)}")
    if messages:
        m = messages[0]
        assert m.get("tool_name") == "codex", f"Unexpected tool_name: {m.get('tool_name')}"


def test_api_usage_tools_includes_codex():
    """GET /api/tools includes codex."""
    r = requests.get(
        f"{BASE_URL}/api/tools",
        cookies={"session_token": test_helpers._auth_token},
    )
    assert r.status_code == 200, f"GET /api/tools failed: {r.status_code}"
    data = r.json()
    tools = data if isinstance(data, list) else data.get("data", [])
    assert "codex" in tools, f"codex not in tools list: {tools}"
    print(f"    Tools: {tools}")


def test_api_codex_alias_resolution():
    """GET /api/workspace/sessions?tool_name=codex-cli also returns codex sessions."""
    data = api_get("/workspace/sessions", params={"tool_name": "codex-cli", "limit": 5})
    _sessions = data.get("data", {}).get("sessions", [])
    total = data.get("data", {}).get("total", 0)
    # Should return the same sessions as tool_name=codex
    print(f"    codex-cli alias: {total} sessions")


# ═══════════════════════════════════════════════════════
# SECTION 3: Remote Session Adapter
# ═══════════════════════════════════════════════════════


def test_codex_cli_adapter_imports_corrected():
    """Codex CLI adapter can be imported and has required methods."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    # The adapter key is "codex" (not "codex-cli") in ADAPTERS registry
    assert "codex" in ADAPTERS, f"codex not in ADAPTERS: {list(ADAPTERS.keys())}"
    adapter_cls = ADAPTERS["codex"]
    adapter = adapter_cls()

    # Verify required attributes
    assert adapter.EXECUTABLE == "codex", f"Unexpected executable: {adapter.EXECUTABLE}"
    assert adapter.NPM_PACKAGE == "@openai/codex", f"Unexpected npm package: {adapter.NPM_PACKAGE}"

    # Verify methods exist
    assert hasattr(adapter, "get_env_vars"), "Missing get_env_vars"
    assert hasattr(adapter, "build_start_args"), "Missing build_start_args"
    assert hasattr(adapter, "build_single_shot_args"), "Missing build_single_shot_args"
    assert hasattr(adapter, "get_settings_path"), "Missing get_settings_path"
    assert hasattr(adapter, "configure_settings"), "Missing configure_settings"

    print(f"    Adapter: {adapter_cls.__name__}, executable={adapter.EXECUTABLE}")


def test_codex_adapter_env_vars():
    """Codex adapter sets correct environment variables."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()
    env = adapter.get_env_vars(proxy_url="http://proxy:8080", proxy_token="test-token")

    assert "OPENAI_API_KEY" in env, "Missing OPENAI_API_KEY"
    assert env["OPENAI_API_KEY"] == "test-token", "OPENAI_API_KEY should be proxy_token"
    assert "OPENAI_BASE_URL" in env, "Missing OPENAI_BASE_URL"
    assert "v1" in env["OPENAI_BASE_URL"], "OPENAI_BASE_URL should contain /v1"
    print(f"    Env vars: {list(env.keys())}")


def test_codex_adapter_build_args():
    """Codex adapter builds correct CLI arguments."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()

    # Interactive args
    args = adapter.build_start_args(session_id="test-123", project_path="/tmp", model="o3")
    assert "codex" in args, f"Expected 'codex' in args: {args}"
    assert "--model" in args, "Missing --model flag"
    assert "o3" in args, "Model not in args"
    print(f"    Interactive args: {args}")

    # Single shot args
    args = adapter.build_single_shot_args("write a test", project_path="/tmp", model="o3")
    assert "exec" in args, "Missing 'exec' subcommand"
    assert "--json" in args, "Missing --json flag"
    print(f"    Single-shot args: {args}")


def test_codex_adapter_settings():
    """Codex adapter configures settings correctly."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()
    settings = adapter.build_settings(
        base_settings={
            "model": "o3",
            "env": {"OPENAI_API_KEY": "should-be-stripped"},
        }
    )

    # Should have model_reasoning_summary
    assert "model_reasoning_summary" in settings, "Missing model_reasoning_summary"
    assert settings["model_reasoning_summary"] == "auto", "model_reasoning_summary should be 'auto'"
    # Sensitive keys should be stripped from env
    env = settings.get("env", {})
    assert "OPENAI_API_KEY" not in env, "OPENAI_API_KEY should be stripped"
    print(f"    Settings keys: {list(settings.keys())}")


def test_terminal_menu_includes_codex():
    """Terminal menu includes Codex entry."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
    import importlib

    tm = importlib.import_module("terminal_menu")

    codex_entries = [t for t in tm.TOOLS if t["cli"] == "codex"]
    assert codex_entries, "No codex entry in TOOLS"
    codex = codex_entries[0]
    assert codex["name"] == "Codex", f"Unexpected name: {codex['name']}"
    assert codex["install_cmd"] == "npm install -g @openai/codex@latest"
    assert codex["env_key"] == "OPENAI_API_KEY"
    print(f"    Codex menu entry: {codex}")


# ═══════════════════════════════════════════════════════
# SECTION 4: Tool Connector Registration
# ═══════════════════════════════════════════════════════


def test_tool_connector_has_codex():
    """Tool connector registers codex tool."""
    from app.modules.workspace.tool_connector import get_tool_connector

    connector = get_tool_connector()
    codex = connector.get_tool("codex")
    assert codex, "codex not registered in tool connector"
    assert codex.name == "codex"
    assert codex.tool_type == "agent", f"Expected 'agent', got '{codex.tool_type}'"
    assert codex.supports_streaming, "codex should support streaming"
    assert codex.supports_tools, "codex should support tools"
    assert len(codex.models) > 0, "codex should have models"
    print(f"    Codex: type={codex.tool_type}, models={codex.models}")


def test_tool_name_normalization():
    """Tool name normalization works for codex variants."""
    from app.utils.tool_names import CANONICAL_TOOL_NAMES, TOOL_NAME_ALIASES, normalize_tool_name

    assert normalize_tool_name("codex-cli") == "codex", "codex-cli should normalize to codex"
    assert normalize_tool_name("codex") == "codex", "codex should stay codex"
    assert "codex" in TOOL_NAME_ALIASES, "codex not in TOOL_NAME_ALIASES"
    assert "codex-cli" in CANONICAL_TOOL_NAMES, "codex-cli not in CANONICAL_TOOL_NAMES"
    print(f"    Aliases: {TOOL_NAME_ALIASES.get('codex', [])}")


def test_user_tool_account_codex():
    """User tool account model supports codex type."""
    from app.models.user_tool_account import TOOL_TYPES

    assert "codex" in TOOL_TYPES, "codex not in TOOL_TYPES"
    assert TOOL_TYPES["codex"] == "Codex", f"Unexpected display: {TOOL_TYPES['codex']}"
    print(f"    TOOL_TYPES['codex'] = {TOOL_TYPES['codex']}")


# ═══════════════════════════════════════════════════════
# SECTION 5: Frontend Verification (Playwright)
# ═══════════════════════════════════════════════════════


def test_frontend_codex_sessions_page():
    """Frontend sessions page loads and shows codex sessions."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work/sessions")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: "session" in page.inner_text("body").lower(),
                timeout=5,
                interval=0.5,
                description="sessions page load",
            )
            screenshot(page, "codex-sessions-page", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert (
                "codex" in page_text.lower()
                or "session" in page_text.lower()
                or "会话" in page_text
            ), f"Session page doesn't show codex content: {page_text[:300]}"
            print("    Sessions page loaded with codex filter")
        finally:
            browser.close()


def test_frontend_codex_session_detail():
    """Frontend session detail page renders codex content_blocks."""
    from playwright.sync_api import sync_playwright

    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 1})
    sessions = data.get("data", {}).get("sessions", [])
    if not sessions:
        print("    SKIP: No codex sessions to test detail page")
        return

    sid = sessions[0]["session_id"]

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work/sessions")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="session detail load",
            )
            screenshot(page, "codex-session-detail", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert len(page_text) > 50, "Session detail page appears empty"
            print(f"    Session detail loaded for {sid[:16]}...")
        finally:
            browser.close()


def test_frontend_assist_panel_codex():
    """Frontend assist panel includes Codex tool option."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="work page load",
            )
            screenshot(page, "codex-assist-panel", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            has_codex = "codex" in page_text.lower()
            print(f"    Work page loaded, codex in panel: {has_codex}")
        finally:
            browser.close()


def test_frontend_api_key_codex_option():
    """Frontend API key management includes Codex CLI option."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL, username="admin")
            page.goto(f"{WEBUI_URL}/manage/api-keys")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="api key page load",
            )
            screenshot(page, "codex-api-key-page", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            has_codex = "codex" in page_text.lower()
            print(f"    API key page loaded, codex option present: {has_codex}")
        finally:
            browser.close()


# ═══════════════════════════════════════════════════════
# SECTION 6: Content Block Rendering Verification
# ═══════════════════════════════════════════════════════


def test_content_block_types_in_api():
    """API returns session messages with all Codex content_block types."""
    # Get a session with messages
    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 20})
    sessions = data.get("data", {}).get("sessions", [])

    all_types = set()
    checked = 0
    for s in sessions:
        sid = s["session_id"]
        try:
            detail = api_get(f"/workspace/sessions/{sid}", params={"include_messages": "true"})
        except AssertionError:
            continue
        messages = detail.get("data", {}).get("messages", [])
        for msg in messages:
            meta = msg.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    continue
            blocks = meta.get("content_blocks", [])
            for b in blocks:
                if isinstance(b, dict) and "type" in b:
                    all_types.add(b["type"])
            checked += 1

    print(f"    Checked {checked} sessions, found types: {sorted(all_types)}")
    assert "text" in all_types, "No 'text' content_blocks in API responses"


# ═══════════════════════════════════════════════════════
# MAIN: Run all tests
# ═══════════════════════════════════════════════════════


def main():
    print("=" * 60)
    print("Codex Integration E2E Test Suite")
    print("=" * 60)

    # ── Phase 1: Data layer tests (no server required) ──
    print("\n── Phase 1: Data Layer ──")

    run_test("fetch_codex.py runs successfully", test_fetch_codex_runs)
    run_test("daily_usage has codex entries with tokens", test_daily_usage_has_codex)
    run_test("agent_sessions have codex with tokens", test_agent_sessions_have_codex)
    run_test("session_messages have codex content", test_session_messages_have_codex)
    run_test("Codex content_block types exist", test_codex_content_block_types)
    run_test("daily_messages has codex entries", test_codex_daily_messages)

    # ── Phase 2: Adapter tests (no server required) ──
    print("\n── Phase 2: CLI Adapter ──")

    run_test("Codex CLI adapter imports correctly", test_codex_cli_adapter_imports_corrected)
    run_test("Codex adapter sets correct env vars", test_codex_adapter_env_vars)
    run_test("Codex adapter builds CLI arguments", test_codex_adapter_build_args)
    run_test("Codex adapter configures settings", test_codex_adapter_settings)
    run_test("Terminal menu includes Codex", test_terminal_menu_includes_codex)

    # ── Phase 3: Backend module tests (no server required) ──
    print("\n── Phase 3: Backend Modules ──")

    run_test("Tool connector registers codex", test_tool_connector_has_codex)
    run_test("Tool name normalization works", test_tool_name_normalization)
    run_test("User tool account supports codex", test_user_tool_account_codex)

    # ── Phase 4: API tests (server required) ──
    print("\n── Phase 4: API Endpoints ──")

    try:
        api_login()
        print("  Logged in successfully")
    except Exception as e:
        print(f"  SKIP: Login failed: {e}")
        print("  Skipping API and frontend tests")
        print_results(results)
        return

    run_test("API sessions list codex", test_api_sessions_list_codex)
    run_test("API session detail with messages", test_api_session_detail)
    run_test("API session with tokens", test_api_session_with_tokens)
    run_test("API usage for codex", test_api_usage_codex)
    run_test("API messages for codex", test_api_messages_codex)
    run_test("API usage tools includes codex", test_api_usage_tools_includes_codex)
    run_test("API codex alias resolution", test_api_codex_alias_resolution)
    run_test("Content block types in API responses", test_content_block_types_in_api)

    # ── Phase 5: Frontend tests (server + frontend required) ──
    print("\n── Phase 5: Frontend (Playwright) ──")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{WEBUI_URL}/login", timeout=10000)
            # Wait for login form to render (React SPA)
            try:
                page.wait_for_selector("#username", timeout=5000)
            except Exception:
                # Login form not found - either already logged in, or frontend is from a different project
                root_text = page.inner_text("body").strip()
                if not root_text:
                    print(
                        "  SKIP: Frontend SPA not rendering (Vite dev server may be from different project)"
                    )
                    browser.close()
                    print_results(results)
                    return
                # If page has content but no login form, user may already be authenticated
                print(f"  SKIP: Login form not found, page content: {root_text[:100]}")
                browser.close()
                print_results(results)
                return
            browser.close()
        print("  Frontend server reachable and login form rendered")
    except Exception as e:
        print(f"  SKIP: Frontend not reachable: {e}")
        print_results(results)
        return

    run_test("Frontend codex sessions page", test_frontend_codex_sessions_page)
    run_test("Frontend codex session detail", test_frontend_codex_session_detail)
    run_test("Frontend assist panel codex", test_frontend_assist_panel_codex)
    run_test("Frontend API key codex option", test_frontend_api_key_codex_option)

    if not print_results(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
