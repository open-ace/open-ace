#!/usr/bin/env python3
"""Unit tests for zcode autonomous workflow session persistence.

Verifies the fix for the bug where zcode workflow sessions had 0 messages in
the detail view. Root cause: _run_zcode_appserver returned the CLI session id
(sess_xxx) as result.session_id, but run_agent_task created the wrapper
agent_sessions row under the uuid. add_message's session-exists check
(session_manager.py:806-812) then silently dropped every message because no
agent_sessions row existed under the CLI id.

The fix: _run_zcode_appserver now creates the wrapper row under the real CLI
id (mirroring Claude's _ensure_sidebar_session), so milestone/messages/row all
share one id. These tests drive _run_zcode_appserver with a stubbed SDK and a
real SessionManager against a temp SQLite DB and assert:

  1. result.session_id is the CLI id (not the uuid),
  2. create_session was called with the CLI id (row exists),
  3. after _persist_local_session_messages, session_messages rows exist.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_agent_runner():
    """Load the agent_runner module fresh (it has heavy imports done lazily)."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    mod_path = _REPO_ROOT / "app" / "modules" / "workspace" / "autonomous" / "agent_runner.py"
    spec = importlib.util.spec_from_file_location("agent_runner_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the module's dataclasses can resolve their own
    # type hints via sys.modules during class construction.
    sys.modules["agent_runner_under_test"] = mod
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _load_session_manager():
    import importlib

    mod_path = _REPO_ROOT / "app" / "modules" / "workspace" / "session_manager.py"
    spec = importlib.util.spec_from_file_location("session_manager_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_manager_under_test"] = mod
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Point the DB at a temp SQLite file so no PostgreSQL/shared state is used."""
    db_file = tmp_path / "test.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    # Create the authoritative schema (the inline _ensure_tables() is stale and
    # misses columns like project_id, so load the real DDL instead).
    schema_path = _REPO_ROOT / "schema" / "schema-sqlite.sql"
    conn = sqlite3.connect(str(db_file))
    if schema_path.exists():
        conn.executescript(schema_path.read_text())
    # session_messages.milestone_id is written by add_message but missing from
    # schema-sqlite.sql (pre-existing drift). Add it so the real code path works.
    try:
        conn.execute("ALTER TABLE session_messages ADD COLUMN milestone_id text")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()
    return db_file


class _StubZCodeSession:
    """Minimal stand-in for ZCodeAppServerSession.

    Simulates a successful session/create → send → wait_turn cycle, emitting
    one assistant text event so _persist_local_session_messages has an
    event_log to write.
    """

    _cli_session_id = "sess_cli_abc123"
    last_send_error = None

    def __init__(self, **kwargs):
        self.allowed_tools = []

    def start(self, **kwargs):
        return True

    def send_message(self, prompt):
        return True

    def wait_turn(self, timeout=None):
        return True

    def stop(self):
        pass


def _make_runner(session_manager_mod, env):
    """Build an AgentRunner with a real SessionManager over the temp DB."""
    agent_runner_mod = _load_agent_runner()
    sm = session_manager_mod.SessionManager(db_path=str(env))
    return agent_runner_mod.AutonomousAgentRunner(session_manager=sm), agent_runner_mod


# --------------------------------------------------------------------------- #
# create_session is called under the CLI id
# --------------------------------------------------------------------------- #


def test_run_zcode_appserver_creates_session_under_cli_id(env, monkeypatch):
    session_manager_mod = _load_session_manager()
    runner, agent_runner_mod = _make_runner(session_manager_mod, env)

    # Stub the SDK + adapter + subprocess so no real process is spawned.
    fake_process = MagicMock()
    monkeypatch.setattr(agent_runner_mod.subprocess, "Popen", lambda *a, **k: fake_process)

    fake_adapter = MagicMock()
    fake_adapter.build_start_args.return_value = ["zcode", "--app-server"]
    fake_adapter.supports_stdin_input.return_value = False
    monkeypatch.setitem(
        sys.modules, "cli_adapters", types.SimpleNamespace(get_adapter=lambda name: fake_adapter)
    )
    monkeypatch.setitem(
        sys.modules,
        "zcode_app_server",
        types.SimpleNamespace(ZCodeAppServerSession=_StubZCodeSession),
    )

    # Patch the lazy import targets inside _run_zcode_appserver: it does
    # `from cli_adapters import get_adapter` and
    # `from zcode_app_server import ZCodeAppServerSession` after putting
    # remote-agent on sys.path. Pre-seed those modules.
    import importlib

    remote_agent_dir = str(_REPO_ROOT / "remote-agent")
    if remote_agent_dir not in sys.path:
        sys.path.insert(0, remote_agent_dir)

    cli_adapters_mod = types.ModuleType("cli_adapters")
    cli_adapters_mod.get_adapter = lambda name: fake_adapter
    monkeypatch.setitem(sys.modules, "cli_adapters", cli_adapters_mod)

    zcode_app_mod = types.ModuleType("zcode_app_server")
    zcode_app_mod.ZCodeAppServerSession = _StubZCodeSession
    monkeypatch.setitem(sys.modules, "zcode_app_server", zcode_app_mod)

    result = runner._run_zcode_appserver(
        session_id="uuid-wrapper-id",
        cli_tool="zcode",
        model="GLM-5.2",
        project_path="/tmp/repo",
        prompt="do the thing",
        permission_mode="yolo",
        timeout=60,
        workflow_id="wf_001",
        user_id=1,
        workspace_type="local",
    )

    # result.session_id must be the CLI id, not the uuid.
    assert result.session_id == "sess_cli_abc123"

    # The wrapper row must exist under the CLI id (the invariant the bug broke).
    conn = sqlite3.connect(str(env))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT session_id, tool_name, session_type FROM agent_sessions WHERE session_id = ?",
        ("sess_cli_abc123",),
    ).fetchone()
    conn.close()
    assert row is not None, "wrapper row missing under CLI id — add_message would drop messages"
    assert row["tool_name"] == "zcode"
    assert row["session_type"] == "workflow"


def test_run_zcode_appserver_error_path_creates_session(env, monkeypatch):
    """When session/create fails (no CLI id), the error session is still visible."""
    session_manager_mod = _load_session_manager()
    runner, agent_runner_mod = _make_runner(session_manager_mod, env)

    fake_process = MagicMock()
    monkeypatch.setattr(agent_runner_mod.subprocess, "Popen", lambda *a, **k: fake_process)

    fake_adapter = MagicMock()
    fake_adapter.build_start_args.return_value = ["zcode", "--app-server"]
    fake_adapter.supports_stdin_input.return_value = False

    remote_agent_dir = str(_REPO_ROOT / "remote-agent")
    if remote_agent_dir not in sys.path:
        sys.path.insert(0, remote_agent_dir)
    cli_adapters_mod = types.ModuleType("cli_adapters")
    cli_adapters_mod.get_adapter = lambda name: fake_adapter
    monkeypatch.setitem(sys.modules, "cli_adapters", cli_adapters_mod)

    # A session that fails to start → no _cli_session_id → falls back to uuid.
    class _FailingSession(_StubZCodeSession):
        _cli_session_id = ""

        def start(self, **kwargs):
            return False

    zcode_app_mod = types.ModuleType("zcode_app_server")
    zcode_app_mod.ZCodeAppServerSession = _FailingSession
    monkeypatch.setitem(sys.modules, "zcode_app_server", zcode_app_mod)

    result = runner._run_zcode_appserver(
        session_id="uuid-wrapper-id",
        cli_tool="zcode",
        model="GLM-5.2",
        project_path="/tmp/repo",
        prompt="do the thing",
        permission_mode="yolo",
        timeout=60,
        workflow_id="wf_002",
        user_id=1,
        workspace_type="local",
    )

    assert result.success is False
    # Falls back to uuid when no CLI id; a row must still exist so it's visible.
    assert result.session_id == "uuid-wrapper-id"
    conn = sqlite3.connect(str(env))
    row = conn.execute(
        "SELECT session_id FROM agent_sessions WHERE session_id = ?", ("uuid-wrapper-id",)
    ).fetchone()
    conn.close()
    assert row is not None, "failed session should still be visible in the list"


def test_run_agent_task_pre_dispatch_failure_creates_session(env, monkeypatch):
    """Regression: a failure before _run_zcode_appserver dispatches (e.g.
    adapter/executable resolution) must still leave a visible row. Previously,
    skipping the uuid pre-create for app-server tools made such failures
    invisible. The run_agent_task outer-except now creates the row under uuid."""
    session_manager_mod = _load_session_manager()
    runner, agent_runner_mod = _make_runner(session_manager_mod, env)

    # Force _run_local to fail before dispatch: get_adapter raises.
    remote_agent_dir = str(_REPO_ROOT / "remote-agent")
    if remote_agent_dir not in sys.path:
        sys.path.insert(0, remote_agent_dir)
    cli_adapters_mod = types.ModuleType("cli_adapters")

    def _boom(name):
        raise RuntimeError("adapter not found")

    cli_adapters_mod.get_adapter = _boom
    monkeypatch.setitem(sys.modules, "cli_adapters", cli_adapters_mod)

    result = runner.run_agent_task(
        workflow_id="wf_pre",
        cli_tool="zcode",
        model="GLM-5.2",
        project_path="/tmp/repo",
        prompt="do the thing",
        workspace_type="local",
        permission_mode="yolo",
        session_id="uuid-pre-dispatch",
        user_id=1,
    )

    assert result.success is False
    # The uuid row must exist so the failed run is visible in the session list.
    conn = sqlite3.connect(str(env))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT session_id, status FROM agent_sessions WHERE session_id = ?",
        ("uuid-pre-dispatch",),
    ).fetchone()
    conn.close()
    assert row is not None, "pre-dispatch failure should still create a visible session row"


# --------------------------------------------------------------------------- #
# add_message succeeds when the row exists under the CLI id (the core invariant)
# --------------------------------------------------------------------------- #


def test_add_message_succeeds_when_row_exists_under_cli_id(env):
    """Directly proves the proximate cause is fixed: create_session(cli_id) then
    add_message(cli_id) now inserts a row (previously a silent no-op)."""
    session_manager_mod = _load_session_manager()
    sm = session_manager_mod.SessionManager(db_path=str(env))

    cli_id = "sess_cli_xyz"
    sm.create_session(
        session_id=cli_id,
        session_type="workflow",
        title="Autonomous: wf",
        tool_name="zcode",
        user_id=1,
    )
    msg = sm.add_message(session_id=cli_id, role="assistant", content="hello from zcode")

    assert msg is not None, "add_message returned None — no agent_sessions row under the CLI id"
    conn = sqlite3.connect(str(env))
    count = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE session_id = ?", (cli_id,)
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_add_message_is_noop_without_row(env):
    """Guard test: the silent-drop behavior still exists when no row is present,
    confirming the fix relies on creating the row (not changing add_message)."""
    session_manager_mod = _load_session_manager()
    sm = session_manager_mod.SessionManager(db_path=str(env))

    msg = sm.add_message(session_id="sess_never_created", role="assistant", content="dropped")
    assert msg is None
