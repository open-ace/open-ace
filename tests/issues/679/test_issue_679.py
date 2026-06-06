#!/usr/bin/env python3
"""
Unit tests for GitHub Issue #679 — harden agent connection trust boundary.

Covers two fixes:
  1. _check_heartbeats prunes stale entries from _connections
  2. register message type validates machine_id against DB
"""

import contextlib
import json
import os
import sqlite3
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-issue-679")

RESULTS = []


def test(name):
    RESULTS.append({"name": name, "passed": False, "detail": ""})
    print(f"\n--- TEST: {name} ---")
    return RESULTS[-1]


def ok(detail=""):
    RESULTS[-1]["passed"] = True
    RESULTS[-1]["detail"] = detail
    print(f"  [PASS] {RESULTS[-1]['name']}" + (f" - {detail}" if detail else ""))


def fail(detail=""):
    RESULTS[-1]["detail"] = detail
    print(f"  [FAIL] {RESULTS[-1]['name']} - {detail}")


def print_summary():
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = sum(1 for r in RESULTS if not r["passed"])
    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(RESULTS)}")
    print(f"{'='*60}")
    for r in RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}" + (f" - {r['detail']}" if r["detail"] else ""))
    return failed == 0


# ── Helpers ──

TMP_DB = None


def make_manager():
    """Create a RemoteAgentManager with a fresh temp SQLite DB."""
    global TMP_DB
    if TMP_DB:
        with contextlib.suppress(OSError):
            os.unlink(TMP_DB)
    TMP_DB = tempfile.mktemp(suffix=".db")

    import app.repositories.database as db_mod

    db_mod.is_postgresql = lambda: False
    db_mod.DB_PATH = TMP_DB
    db_mod.DEFAULT_SQLITE_PATH = TMP_DB

    import app.modules.workspace.remote_agent_manager as ram_compat

    ram_compat.is_postgresql = lambda: False

    import app.modules.workspace.session_manager as sm_compat

    sm_compat.DB_PATH = TMP_DB

    import app.modules.workspace.remote_agent_manager as ram_mod

    ram_mod._agent_manager = None

    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    mgr = RemoteAgentManager(db_path=TMP_DB)

    conn = sqlite3.connect(TMP_DB)
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


def _make_app(mgr):
    """Create a minimal Flask app with remote_bp for route testing."""
    import app.repositories.database as db_mod

    db_mod.is_postgresql = lambda: False
    db_mod.DB_PATH = TMP_DB

    from flask import Flask

    import app.modules.workspace.remote_agent_manager as ram_mod
    from app.routes import remote as remote_mod

    ram_mod._agent_manager = mgr

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

    from app.auth import decorators as auth_dec

    _original_load_user = auth_dec._load_user_from_token

    def _mock_load_user(token):
        if not token:
            return None
        if token.startswith("test-token-"):
            parts = token.split("-")
            if len(parts) >= 4:
                return {
                    "id": int(parts[2]),
                    "username": f"user{parts[2]}",
                    "email": f"user{parts[2]}@test.com",
                    "role": parts[3],
                }
        return None

    auth_dec._load_user_from_token = _mock_load_user
    remote_mod._load_user_from_token = _mock_load_user
    app._auth_dec = auth_dec
    app._remote_mod = remote_mod
    app._original_load_user = _original_load_user

    return app


# ── Fix 1 Tests: _check_heartbeats prunes _connections ──


def test_check_heartbeats_prunes_stale_connections():
    """Stale machines (offline + heartbeat too old) are removed from _connections."""
    test("Fix1: _check_heartbeats prunes stale _connections entries")
    mgr = make_manager()

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(seconds=300)  # well past 180s timeout

    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        # Machine offline with old heartbeat — should be pruned
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'offline', ?, ?, ?)",
            ("mid-stale", "stale-machine", old.isoformat(), now.isoformat(), now.isoformat()),
        )
        # Machine online with recent heartbeat — should NOT be pruned
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'online', ?, ?, ?)",
            ("mid-live", "live-machine", now.isoformat(), now.isoformat(), now.isoformat()),
        )
        conn.commit()

    # Add both to _connections manually
    mgr._connections["mid-stale"] = None
    mgr._connections["mid-live"] = None

    mgr._check_heartbeats()

    if "mid-stale" not in mgr._connections and "mid-live" in mgr._connections:
        ok("stale removed, live kept")
    else:
        stale_in = "mid-stale" in mgr._connections
        live_in = "mid-live" in mgr._connections
        fail(f"stale_in={stale_in}, live_in={live_in}")


def test_check_heartbeats_keeps_online_connections():
    """Online machines are never removed from _connections by _check_heartbeats."""
    test("Fix1: online machines remain in _connections after heartbeat check")
    mgr = make_manager()

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'online', ?, ?, ?)",
            ("mid-online", "online-machine", now.isoformat(), now.isoformat(), now.isoformat()),
        )
        conn.commit()

    mgr._connections["mid-online"] = None
    mgr._check_heartbeats()

    if "mid-online" in mgr._connections:
        ok("online machine kept in _connections")
    else:
        fail("online machine was incorrectly removed")


def test_check_heartbeats_no_prune_recently_offline():
    """Machine just went offline (< cutoff) is NOT pruned from _connections."""
    test("Fix1: recently offline machine not pruned (within timeout)")
    mgr = make_manager()

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Heartbeat only 30s ago — within the 180s timeout, so should NOT be pruned
    recent = now - timedelta(seconds=30)

    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'offline', ?, ?, ?)",
            ("mid-recent", "recent-offline", recent.isoformat(), now.isoformat(), now.isoformat()),
        )
        conn.commit()

    mgr._connections["mid-recent"] = None
    mgr._check_heartbeats()

    if "mid-recent" in mgr._connections:
        ok("recently offline machine kept in _connections")
    else:
        fail("recently offline machine was incorrectly pruned")


# ── Fix 2 Tests: register message validates machine_id ──


def test_register_unknown_machine_returns_404():
    """POST /agent/message with unknown machine_id returns 404."""
    test("Fix2: register with unknown machine_id returns 404")
    mgr = make_manager()
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = client.post(
            "/api/remote/agent/message",
            json={"type": "register", "machine_id": "nonexistent-machine"},
        )
        if resp.status_code == 404:
            body = resp.get_json()
            if body and "error" in body:
                ok(f"status={resp.status_code}, error={body['error']}")
            else:
                fail(f"status=404 but unexpected body: {body}")
        else:
            fail(f"expected 404, got {resp.status_code}: {resp.get_json()}")


def test_register_known_machine_returns_200():
    """POST /agent/message with valid machine_id returns 200."""
    test("Fix2: register with valid machine_id returns 200")
    mgr = make_manager()

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'offline', ?, ?, ?)",
            ("mid-valid", "valid-machine", now.isoformat(), now.isoformat(), now.isoformat()),
        )
        conn.commit()

    app = _make_app(mgr)

    with app.test_client() as client:
        resp = client.post(
            "/api/remote/agent/message",
            json={"type": "register", "machine_id": "mid-valid"},
        )
        if resp.status_code == 200:
            body = resp.get_json()
            if body and body.get("success"):
                ok(f"status=200, type={body.get('type')}")
            else:
                fail(f"status=200 but unexpected body: {body}")
        else:
            fail(f"expected 200, got {resp.status_code}: {resp.get_json()}")


def test_register_unknown_machine_not_in_connections():
    """Unknown machine_id is NOT added to _connections after rejected register."""
    test("Fix2: unknown machine_id not added to _connections")
    mgr = make_manager()
    app = _make_app(mgr)

    with app.test_client() as client:
        client.post(
            "/api/remote/agent/message",
            json={"type": "register", "machine_id": "fake-machine"},
        )
        # Even after the request (regardless of status), _connections should not have it
        if "fake-machine" not in mgr._connections:
            ok("fake machine_id not in _connections")
        else:
            fail("fake machine_id was added to _connections!")


# ── Run ──

if __name__ == "__main__":
    try:
        test_check_heartbeats_prunes_stale_connections()
        test_check_heartbeats_keeps_online_connections()
        test_check_heartbeats_no_prune_recently_offline()
        test_register_unknown_machine_returns_404()
        test_register_known_machine_returns_200()
        test_register_unknown_machine_not_in_connections()
    finally:
        # Clean up temp DB
        if TMP_DB:
            with contextlib.suppress(OSError):
                os.unlink(TMP_DB)

    success = print_summary()
    sys.exit(0 if success else 1)
