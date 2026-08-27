"""Heartbeat trust-boundary tests for the remote agent manager (Issue #679).

Fix 1 of #679: ``RemoteAgentManager._check_heartbeats`` prunes stale entries
from the process-local ``_connections`` map, so ``is_connected()`` cannot keep
reporting a machine as connected after its heartbeat went stale.

Migrated from tests/issues/679/test_issue_679.py (the three
``_check_heartbeats`` tests), rewritten from the print-based ok()/fail()
harness to real pytest asserts — every former ok()/fail() predicate is now an
assert. Hermeticity fixes vs. the legacy harness:
- ``tempfile.mktemp`` -> ``tmp_path`` fixture;
- destructive never-restored global mutations (``db_mod.DB_PATH``,
  ``DEFAULT_SQLITE_PATH``, ``is_postgresql``, ``ram_compat.is_postgresql``,
  ``sm_compat.DB_PATH``, ``ram_mod._agent_manager``) -> ``monkeypatch``;
- ``db_mod.CONFIG_DIR`` is pointed at ``tmp_path`` so ``ensure_db_dir()``
  never touches the ambient ``~/.open-ace`` directory;
- dropped ``os.environ.setdefault("SECRET_KEY", ...)`` (no Flask app here).
The autouse ``_isolated_unit_db`` fixture in tests/unit/conftest.py pins
``DATABASE_URL`` to a throwaway SQLite path, so dynamic
``is_postgresql()``/``get_database_url()`` calls never read the ambient
``~/.open-ace/config.json``.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import app.modules.workspace.remote_agent_manager as ram_mod
import app.modules.workspace.session_manager as sm_compat
import app.repositories.database as db_mod

pytestmark = [pytest.mark.regression, pytest.mark.issue(679)]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """Create a RemoteAgentManager with a fresh temp SQLite DB (Issue #679).

    All legacy global patching is scoped via monkeypatch (auto-restored).
    """
    db_path = str(tmp_path / "remote-agent-679.db")

    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "DEFAULT_SQLITE_PATH", db_path)
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(sm_compat, "DB_PATH", db_path)
    # Reset the manager singleton so the route module would see our instance.
    monkeypatch.setattr(ram_mod, "_agent_manager", None)
    # Keep ensure_db_dir()'s makedirs inside tmp_path (never ~/.open-ace).
    monkeypatch.setattr(db_mod, "CONFIG_DIR", str(tmp_path))

    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    mgr = RemoteAgentManager(db_path=db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS remote_machines ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL UNIQUE, "
        "machine_name TEXT, "
        "hostname TEXT, "
        "os_type TEXT, "
        "os_version TEXT, "
        "ip_address TEXT, "
        "status TEXT DEFAULT 'offline', "
        "agent_version TEXT, "
        "capabilities TEXT, "
        "cli_path TEXT, "
        "work_dir TEXT, "
        "tenant_id INTEGER, "
        "created_by INTEGER, "
        "last_heartbeat TIMESTAMP, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL UNIQUE, "
        "session_type TEXT DEFAULT 'chat', "
        "title TEXT, "
        "tool_name TEXT, "
        "host_name TEXT, "
        "user_id INTEGER, "
        "status TEXT DEFAULT 'active', "
        "context TEXT, "
        "settings TEXT, "
        "project_id TEXT, "
        "project_path TEXT, "
        "total_tokens INTEGER DEFAULT 0, "
        "total_input_tokens INTEGER DEFAULT 0, "
        "total_output_tokens INTEGER DEFAULT 0, "
        "message_count INTEGER DEFAULT 0, "
        "request_count INTEGER DEFAULT 0, "
        "model TEXT, "
        "tags TEXT, "
        "workspace_type TEXT DEFAULT 'local', "
        "remote_machine_id TEXT, "
        "paused_at TIMESTAMP, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP, "
        "completed_at TIMESTAMP, "
        "expires_at TIMESTAMP)"
    )
    conn.commit()
    conn.close()
    return mgr


def _insert_machine(mgr, machine_id, machine_name, status, last_heartbeat):
    """Insert a remote_machines row with explicit heartbeat timestamp."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (machine_id, machine_name, status, last_heartbeat, now.isoformat(), now.isoformat()),
        )
        conn.commit()


# ── Fix 1 Tests: _check_heartbeats prunes _connections ──


def test_check_heartbeats_prunes_stale_connections(manager):
    """Stale machines (offline + heartbeat too old) are removed from _connections."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(seconds=300)  # well past 180s timeout

    # Machine offline with old heartbeat — should be pruned
    _insert_machine(manager, "mid-stale", "stale-machine", "offline", old.isoformat())
    # Machine online with recent heartbeat — should NOT be pruned
    _insert_machine(manager, "mid-live", "live-machine", "online", now.isoformat())

    # Add both to _connections manually
    manager._connections["mid-stale"] = None
    manager._connections["mid-live"] = None

    manager._check_heartbeats()

    assert "mid-stale" not in manager._connections, "stale machine must be pruned"
    assert "mid-live" in manager._connections, "live machine must be kept"


def test_check_heartbeats_keeps_online_connections(manager):
    """Online machines are never removed from _connections by _check_heartbeats."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    _insert_machine(manager, "mid-online", "online-machine", "online", now.isoformat())

    manager._connections["mid-online"] = None
    manager._check_heartbeats()

    assert "mid-online" in manager._connections, "online machine was incorrectly removed"


def test_check_heartbeats_no_prune_recently_offline(manager):
    """Machine just went offline (< cutoff) is NOT pruned from _connections."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Heartbeat only 30s ago — within the 180s timeout, so should NOT be pruned
    recent = now - timedelta(seconds=30)

    _insert_machine(manager, "mid-recent", "recent-offline", "offline", recent.isoformat())

    manager._connections["mid-recent"] = None
    manager._check_heartbeats()

    assert "mid-recent" in manager._connections, "recently offline machine was incorrectly pruned"
