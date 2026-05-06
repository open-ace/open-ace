#!/usr/bin/env python3
"""
Unit tests for hostname-based deduplication during remote machine registration.

Tests:
  - New machine registration (no duplicate) -> normal INSERT
  - Same hostname re-registration (old record offline) -> merge
  - Same hostname re-registration (old record online) -> error
  - Empty hostname -> no merge, normal INSERT
  - Merge preserves machine_assignments
  - Merge preserves agent_sessionss
  - Merge cleans up in-memory state
"""

import contextlib
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

TMP_DB = tempfile.mktemp(suffix=".db")

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


def make_manager():
    """Create a RemoteAgentManager with a fresh temp SQLite DB."""
    global TMP_DB
    with contextlib.suppress(OSError):
        os.unlink(TMP_DB)
    TMP_DB = tempfile.mktemp(suffix=".db")

    # Pre-populate scripts.shared.config to avoid module-level import chain issues
    import types

    if "scripts.shared.config" not in sys.modules:
        mod = types.ModuleType("scripts.shared.config")
        mod.get_database_url = lambda: f"sqlite:///{TMP_DB}"
        sys.modules["scripts.shared.config"] = mod
    if "scripts.shared" not in sys.modules:
        mod = types.ModuleType("scripts.shared")
        sys.modules["scripts.shared"] = mod

    import app.repositories.database as db_mod

    original_is_pg = db_mod.is_postgresql
    original_db_path = db_mod.DB_PATH
    original_get_url = db_mod.get_database_url
    db_mod.is_postgresql = lambda: False
    db_mod.DB_PATH = TMP_DB
    db_mod.get_database_url = lambda: f"sqlite:///{TMP_DB}"

    # Create required tables BEFORE constructing the manager
    import sqlite3

    conn = sqlite3.connect(TMP_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS remote_machines ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL UNIQUE, "
        "machine_name TEXT NOT NULL, "
        "hostname TEXT, "
        "os_type TEXT, "
        "os_version TEXT, "
        "ip_address TEXT, "
        "status TEXT DEFAULT 'offline', "
        "agent_version TEXT, "
        "capabilities TEXT, "
        "tenant_id INTEGER, "
        "created_by INTEGER, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP, "
        "last_heartbeat TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS machine_assignments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL, "
        "user_id INTEGER NOT NULL, "
        "permission TEXT NOT NULL DEFAULT 'user', "
        "granted_by INTEGER, "
        "granted_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_sessionss ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL UNIQUE, "
        "user_id INTEGER, "
        "status TEXT DEFAULT 'active', "
        "remote_machine_id TEXT, "
        "workspace_type TEXT, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (" "id INTEGER PRIMARY KEY, " "username TEXT NOT NULL)"
    )
    conn.execute("INSERT OR IGNORE INTO users (id, username) VALUES (1, 'admin')")
    conn.execute("INSERT OR IGNORE INTO users (id, username) VALUES (2, 'user2')")
    conn.execute("INSERT OR IGNORE INTO users (id, username) VALUES (3, 'user3')")
    conn.commit()
    conn.close()

    import app.modules.workspace.remote_agent_manager as ram_mod

    ram_mod._agent_manager = None

    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    mgr = RemoteAgentManager(db_path=TMP_DB)

    db_mod.is_postgresql = original_is_pg
    db_mod.DB_PATH = original_db_path
    db_mod.get_database_url = original_get_url
    return mgr


def insert_machine(
    mgr,
    machine_id,
    machine_name,
    hostname,
    status="online",
    tenant_id=1,
    updated_at="2026-01-01T00:00:00",
):
    """Helper to insert a machine directly into the DB."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, hostname, status, tenant_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (machine_id, machine_name, hostname, status, tenant_id, updated_at, updated_at),
        )
        conn.commit()


def insert_assignment(mgr, machine_id, user_id, permission="user"):
    """Helper to insert a machine assignment."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at) "
            "VALUES (?, ?, ?, 1, '2026-01-01T00:00:00')",
            (machine_id, user_id, permission),
        )
        conn.commit()


def insert_session(mgr, session_id, remote_machine_id, status="active"):
    """Helper to insert an agent session."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agent_sessions (session_id, user_id, status, remote_machine_id, workspace_type, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, 'remote', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            (session_id, status, remote_machine_id),
        )
        conn.commit()


def create_token(mgr, tenant_id=1, created_by=1):
    """Helper to create a registration token."""
    return mgr.create_registration_token(tenant_id, created_by)


def count_machines_by_hostname(mgr, hostname):
    """Count machines with given hostname."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM remote_machines WHERE hostname = ?",
            (hostname,),
        )
        return cursor.fetchone()["cnt"]


def get_machine(mgr, machine_id):
    """Get a machine by machine_id."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM remote_machines WHERE machine_id = ?",
            (machine_id,),
        )
        return cursor.fetchone()


# ════════════════════════════════════════════
#  Tests
# ════════════════════════════════════════════


def test_new_machine_no_duplicate():
    """New machine registration with unique hostname should work normally."""
    test("new machine registration - no duplicate")
    mgr = make_manager()
    token = create_token(mgr)

    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-001",
        machine_name="host1",
        hostname="host1.example.com",
        ip_address="10.0.0.1",
    )

    if result and result.get("machine_id") == "new-uuid-001" and result.get("status") == "online":
        ok()
    else:
        fail(f"unexpected result: {result}")


def test_merge_offline_duplicate():
    """Re-registering with same hostname where old record is offline should merge."""
    test("merge when old record is offline")
    mgr = make_manager()

    # Insert old offline machine
    insert_machine(
        mgr,
        "old-uuid-001",
        "node237",
        "node237",
        status="offline",
        updated_at="2026-04-27T07:00:00",
    )

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-002",
        machine_name="node237",
        hostname="node237",
        ip_address="10.0.0.2",
    )

    # Should succeed with new machine_id
    if not result or result.get("error"):
        fail(f"expected success, got: {result}")
        return

    if result["machine_id"] != "new-uuid-002":
        fail(f"expected new-uuid-002, got {result['machine_id']}")
        return

    # Old machine_id should no longer exist
    cnt = count_machines_by_hostname(mgr, "node237")
    if cnt != 1:
        fail(f"expected 1 machine, got {cnt}")
        return

    # The surviving record should have new machine_id
    machine = get_machine(mgr, "new-uuid-002")
    if not machine:
        fail("surviving machine not found with new machine_id")
        return

    if machine["status"] != "online":
        fail(f"expected online, got {machine['status']}")
        return

    ok(f"merged, old_id gone, new_id online, count={cnt}")


def test_reject_online_duplicate():
    """Re-registering with same hostname where old record is online should return error."""
    test("reject when old record is online")
    mgr = make_manager()

    insert_machine(mgr, "online-uuid-001", "node237", "node237", status="online")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-003",
        machine_name="node237",
        hostname="node237",
    )

    if result and result.get("error") == "hostname_conflict":
        # Verify original record is untouched
        cnt = count_machines_by_hostname(mgr, "node237")
        if cnt != 1:
            fail(f"expected 1 machine, got {cnt}")
            return
        ok("returned hostname_conflict error")
    else:
        fail(f"expected hostname_conflict error, got: {result}")


def test_empty_hostname_no_merge():
    """Empty hostname should not trigger merge logic."""
    test("empty hostname - no merge attempt")
    mgr = make_manager()

    # Insert old offline machine with same machine_name but no hostname match
    insert_machine(mgr, "old-uuid-nohost", "host-x", None, status="offline")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-nohost",
        machine_name="host-x",
        hostname=None,
    )

    if result and result.get("machine_id") == "new-uuid-nohost":
        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM remote_machines")
            cnt = cursor.fetchone()["cnt"]
        # Both should exist since no merge happened
        if cnt == 2:
            ok("no merge for null hostname, both records exist")
        else:
            fail(f"expected 2 records, got {cnt}")
    else:
        fail(f"unexpected result: {result}")


def test_merge_preserves_assignments():
    """Merge should migrate machine_assignments from old to new machine_id."""
    test("merge preserves machine_assignments")
    mgr = make_manager()

    insert_machine(mgr, "old-uuid-asn", "node500", "node500", status="offline")
    insert_assignment(mgr, "old-uuid-asn", 2, "admin")
    insert_assignment(mgr, "old-uuid-asn", 3, "user")

    token = create_token(mgr, created_by=1)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-asn",
        machine_name="node500",
        hostname="node500",
    )

    if not result or result.get("error"):
        fail(f"registration failed: {result}")
        return

    # Check assignments migrated to new machine_id
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, permission FROM machine_assignments WHERE machine_id = ?",
            ("new-uuid-asn",),
        )
        assignments = {r["user_id"]: r["permission"] for r in cursor.fetchall()}

    if 2 in assignments and 3 in assignments:
        ok(f"assignments migrated: {assignments}")
    else:
        fail(f"expected user 2 and 3 assignments, got: {assignments}")


def test_merge_preserves_sessions():
    """Merge should migrate agent_sessions from old to new machine_id."""
    test("merge preserves agent_sessions")
    mgr = make_manager()

    # Create agent_sessions table for this test
    import sqlite3

    conn = sqlite3.connect(TMP_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL UNIQUE, "
        "user_id INTEGER, "
        "status TEXT DEFAULT 'active', "
        "remote_machine_id TEXT, "
        "workspace_type TEXT, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    insert_machine(mgr, "old-uuid-ses", "node600", "node600", status="offline")
    insert_session(mgr, "sess-001", "old-uuid-ses", "active")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-ses",
        machine_name="node600",
        hostname="node600",
    )

    if not result or result.get("error"):
        fail(f"registration failed: {result}")
        return

    # Check session migrated
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT remote_machine_id FROM agent_sessions WHERE session_id = ?",
            ("sess-001",),
        )
        row = cursor.fetchone()

    if row and row["remote_machine_id"] == "new-uuid-ses":
        ok("session remote_machine_id updated")
    else:
        fail(f"expected new-uuid-ses, got: {row}")


def test_merge_cleans_in_memory_state():
    """Merge should clean up in-memory state for old machine_id."""
    test("merge cleans in-memory state")
    mgr = make_manager()

    insert_machine(mgr, "old-uuid-mem", "node700", "node700", status="offline")

    # Simulate in-memory state for old machine
    with mgr._lock:
        mgr._command_queues["old-uuid-mem"] = [{"type": "test"}]
        mgr._last_heartbeat_db_write["old-uuid-mem"] = 12345.0
        mgr._session_machines["sess-mem"] = "old-uuid-mem"

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-mem",
        machine_name="node700",
        hostname="node700",
    )

    if not result or result.get("error"):
        fail(f"registration failed: {result}")
        return

    # Check old machine_id is gone from in-memory state
    old_in_queues = "old-uuid-mem" in mgr._command_queues
    old_in_heartbeat = "old-uuid-mem" in mgr._last_heartbeat_db_write
    session_updated = mgr._session_machines.get("sess-mem") == "new-uuid-mem"

    if not old_in_queues and not old_in_heartbeat and session_updated:
        ok("in-memory state cleaned and session remapped")
    else:
        fail(
            f"queues={old_in_queues}, heartbeat={old_in_heartbeat}, "
            f"session={mgr._session_machines.get('sess-mem')}"
        )


def test_different_tenant_no_merge():
    """Same hostname in different tenant should not merge."""
    test("different tenant - no merge")
    mgr = make_manager()

    # Machine in tenant 1
    insert_machine(mgr, "t1-uuid", "node800", "node800", status="offline", tenant_id=1)

    # Register in tenant 2
    token = create_token(mgr, tenant_id=2)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="t2-uuid",
        machine_name="node800",
        hostname="node800",
    )

    if not result or result.get("error"):
        fail(f"registration failed: {result}")
        return

    cnt = count_machines_by_hostname(mgr, "node800")
    if cnt == 2:
        ok("different tenants, both records exist")
    else:
        fail(f"expected 2 records, got {cnt}")


def test_invalid_token():
    """Invalid token should still return None."""
    test("invalid registration token returns None")
    mgr = make_manager()

    result = mgr.register_machine(
        registration_token="bad-token",
        machine_id="some-uuid",
        machine_name="test",
        hostname="test",
    )

    if result is None:
        ok()
    else:
        fail(f"expected None, got: {result}")


# ════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════

if __name__ == "__main__":
    test_new_machine_no_duplicate()
    test_merge_offline_duplicate()
    test_reject_online_duplicate()
    test_empty_hostname_no_merge()
    test_merge_preserves_assignments()
    test_merge_preserves_sessions()
    test_merge_cleans_in_memory_state()
    test_different_tenant_no_merge()
    test_invalid_token()

    success = print_summary()
    sys.exit(0 if success else 1)
