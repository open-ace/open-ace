"""Hostname-based deduplication during remote machine registration.

Tests:
  - New machine registration (no duplicate) -> normal INSERT
  - Same hostname re-registration (old record offline) -> merge
  - Same hostname re-registration (old record online) -> error
  - Empty hostname -> no merge, normal INSERT
  - Merge preserves machine_assignments
  - Merge preserves agent_sessions
  - Merge cleans up in-memory state
"""

import pytest

import app.modules.workspace.remote_agent_manager as ram_mod
from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.repositories import database as db_mod
from app.repositories.schema_init import load_schema_from_file

pytestmark = [pytest.mark.regression, pytest.mark.issue(212)]


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """A RemoteAgentManager on a fresh authoritative-schema SQLite DB.

    Background pollers are disabled (they would leak one greenlet pair plus a
    daemon thread per construction across the suite) and both ``is_postgresql``
    flags are forced off so an ambient Postgres config cannot leak ``%s``
    placeholders into the sqlite SQL.
    """
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    monkeypatch.setattr(RemoteAgentManager, "_start_retention_cleanup", lambda self: None)
    monkeypatch.setattr(RemoteAgentManager, "_start_pending_revoke_cleanup", lambda self: None)
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)

    db_path = str(tmp_path / "hostname_dedup.db")
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")

    # Seed the referenced users (authoritative users table requires
    # password_hash; role defaults satisfy the #2332 CHECK constraints).
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (1, "Hostname Tenant 1", "hostname-tenant-1", '{"max_users": 100}'),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (2, "Hostname Tenant 2", "hostname-tenant-2", '{"max_users": 100}'),
    )
    for uid, name in ((1, "admin"), (2, "user2"), (3, "user3")):
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (uid, name, "seed-hash"),
        )
    conn.commit()
    conn.close()

    ram_mod._agent_manager = None
    return RemoteAgentManager(db_path=db_path)


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
            "INSERT INTO agent_sessions "
            "(session_id, user_id, status, remote_machine_id, workspace_type, tool_name, "
            "created_at, updated_at) "
            "VALUES (?, 1, ?, ?, 'remote', 'qwen', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
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


def test_fixture_creates_registration_tokens_table(mgr):
    """Lock the registration_tokens escape hatch (#2457).

    The authoritative schema must provide the registration_tokens table, or
    every test that calls create_token() crashes with 'no such table:
    registration_tokens' (the prior 9-test cluster). A direct schema
    assertion fails fast and clearly at the fixture level instead of
    cascading 9 cryptic OperationalError crashes if the table is ever dropped.
    """
    with mgr.db.connection() as conn:
        row = (
            conn.cursor()
            .execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='registration_tokens'"
            )
            .fetchone()
        )
    assert row is not None, (
        "the authoritative schema must create the registration_tokens table "
        "(create_registration_token reads/writes it)"
    )


def test_new_machine_no_duplicate(mgr):
    """New machine registration with unique hostname should work normally."""
    token = create_token(mgr)

    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-001",
        machine_name="host1",
        hostname="host1.example.com",
        ip_address="10.0.0.1",
    )

    assert result, f"unexpected result: {result}"
    assert result.get("machine_id") == "new-uuid-001", f"unexpected result: {result}"
    assert result.get("status") == "online", f"unexpected result: {result}"


def test_merge_offline_duplicate(mgr):
    """Re-registering with same hostname where old record is offline should merge."""
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
    assert result, f"expected success, got: {result}"
    assert not result.get("error"), f"expected success, got: {result}"
    assert (
        result["machine_id"] == "new-uuid-002"
    ), f"expected new-uuid-002, got {result['machine_id']}"

    # Old machine_id should no longer exist
    cnt = count_machines_by_hostname(mgr, "node237")
    assert cnt == 1, f"expected 1 machine, got {cnt}"

    # The surviving record should have new machine_id
    machine = get_machine(mgr, "new-uuid-002")
    assert machine is not None, "surviving machine not found with new machine_id"
    assert machine["status"] == "online", f"expected online, got {machine['status']}"


def test_reject_online_duplicate(mgr):
    """Re-registering with same hostname where old record is online should return error."""
    insert_machine(mgr, "online-uuid-001", "node237", "node237", status="online")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-003",
        machine_name="node237",
        hostname="node237",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error, got: {result}"
    # Verify original record is untouched
    cnt = count_machines_by_hostname(mgr, "node237")
    assert cnt == 1, f"expected 1 machine, got {cnt}"


def test_empty_hostname_no_merge(mgr):
    """Empty hostname should not trigger merge logic."""
    # Insert old offline machine with same machine_name but no hostname match
    insert_machine(mgr, "old-uuid-nohost", "host-x", None, status="offline")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-nohost",
        machine_name="host-x",
        hostname=None,
    )

    assert result, f"unexpected result: {result}"
    assert result.get("machine_id") == "new-uuid-nohost", f"unexpected result: {result}"
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM remote_machines")
        cnt = cursor.fetchone()["cnt"]
    # Both should exist since no merge happened
    assert cnt == 2, f"expected 2 records (no merge for null hostname), got {cnt}"


def test_merge_preserves_assignments(mgr):
    """Merge should migrate machine_assignments from old to new machine_id."""
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

    assert result, f"registration failed: {result}"
    assert not result.get("error"), f"registration failed: {result}"

    # Check assignments migrated to new machine_id
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, permission FROM machine_assignments WHERE machine_id = ?",
            ("new-uuid-asn",),
        )
        assignments = {r["user_id"]: r["permission"] for r in cursor.fetchall()}

    assert (
        2 in assignments and 3 in assignments
    ), f"expected user 2 and 3 assignments migrated, got: {assignments}"


def test_merge_preserves_sessions(mgr):
    """Merge should migrate agent_sessions from old to new machine_id."""
    insert_machine(mgr, "old-uuid-ses", "node600", "node600", status="offline")
    insert_session(mgr, "sess-001", "old-uuid-ses", "active")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-ses",
        machine_name="node600",
        hostname="node600",
    )

    assert result, f"registration failed: {result}"
    assert not result.get("error"), f"registration failed: {result}"

    # Check session migrated
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT remote_machine_id FROM agent_sessions WHERE session_id = ?",
            ("sess-001",),
        )
        row = cursor.fetchone()

    assert row is not None, "session row vanished during merge"
    assert (
        row["remote_machine_id"] == "new-uuid-ses"
    ), f"expected new-uuid-ses, got {row['remote_machine_id'] if row else row}"


def test_merge_cleans_in_memory_state(mgr):
    """Merge should clean up in-memory state for old machine_id."""
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

    assert result, f"registration failed: {result}"
    assert not result.get("error"), f"registration failed: {result}"

    # Check old machine_id is gone from in-memory state
    assert "old-uuid-mem" not in mgr._command_queues
    assert "old-uuid-mem" not in mgr._last_heartbeat_db_write
    assert mgr._session_machines.get("sess-mem") == "new-uuid-mem"


def test_different_tenant_no_merge(mgr):
    """Same hostname in different tenant should not merge."""
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

    assert result, f"registration failed: {result}"
    assert not result.get("error"), f"registration failed: {result}"

    cnt = count_machines_by_hostname(mgr, "node800")
    assert cnt == 2, f"expected 2 records (different tenants), got {cnt}"


def test_invalid_token(mgr):
    """Invalid token should still return None."""
    result = mgr.register_machine(
        registration_token="bad-token",
        machine_id="some-uuid",
        machine_name="test",
        hostname="test",
    )

    assert result is None, f"expected None, got: {result}"


@pytest.mark.issue(2537)
def test_reject_idle_duplicate(mgr):
    """Re-registering with same hostname where old record is idle should return error (Issue #2537)."""
    insert_machine(mgr, "idle-uuid-001", "node-idle", "node-idle", status="idle")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-idle",
        machine_name="node-idle",
        hostname="node-idle",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error, got: {result}"
    # Verify original record is untouched
    cnt = count_machines_by_hostname(mgr, "node-idle")
    assert cnt == 1, f"expected 1 machine, got {cnt}"
    # Verify error payload carries the conflicting status
    assert (
        result.get("conflicting_status") == "idle"
    ), f"expected conflicting_status=idle, got: {result}"


@pytest.mark.issue(2537)
def test_reject_busy_duplicate(mgr):
    """Re-registering with same hostname where old record is busy should return error (Issue #2537)."""
    insert_machine(mgr, "busy-uuid-001", "node-busy", "node-busy", status="busy")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-busy",
        machine_name="node-busy",
        hostname="node-busy",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error, got: {result}"
    # Verify original record is untouched
    cnt = count_machines_by_hostname(mgr, "node-busy")
    assert cnt == 1, f"expected 1 machine, got {cnt}"
    # Verify error payload carries the conflicting status
    assert (
        result.get("conflicting_status") == "busy"
    ), f"expected conflicting_status=busy, got: {result}"


@pytest.mark.issue(2537)
def test_reject_null_status_duplicate(mgr):
    """Re-registering with same hostname where old record has NULL status should return error (Issue #2537)."""
    # Insert machine with NULL status
    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, hostname, status, tenant_id, created_at, updated_at) "
            "VALUES (?, ?, ?, NULL, 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            ("null-uuid-001", "node-null", "node-null"),
        )
        conn.commit()

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-null",
        machine_name="node-null",
        hostname="node-null",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error (NULL status, conservative), got: {result}"
    # Verify original record is untouched
    cnt = count_machines_by_hostname(mgr, "node-null")
    assert cnt == 1, f"expected 1 machine, got {cnt}"


@pytest.mark.issue(2537)
def test_reject_unknown_status_duplicate(mgr):
    """Re-registering with same hostname where old record has unknown status should return error (Issue #2537)."""
    # Insert machine with unknown status
    insert_machine(mgr, "unknown-uuid-001", "node-unknown", "node-unknown", status="error")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-unknown",
        machine_name="node-unknown",
        hostname="node-unknown",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error (unknown status, conservative), got: {result}"
    # Verify original record is untouched
    cnt = count_machines_by_hostname(mgr, "node-unknown")
    assert cnt == 1, f"expected 1 machine, got {cnt}"
    # Verify error payload carries the conflicting status
    assert (
        result.get("conflicting_status") == "error"
    ), f"expected conflicting_status=error, got: {result}"


@pytest.mark.issue(2537)
def test_error_message_contains_details(mgr):
    """Error message should contain conflicting_machine_id and conflicting_status (Issue #2537)."""
    insert_machine(mgr, "detail-uuid-001", "node-detail", "node-detail", status="idle")

    token = create_token(mgr)
    result = mgr.register_machine(
        registration_token=token,
        machine_id="new-uuid-detail",
        machine_name="node-detail",
        hostname="node-detail",
    )

    assert result, f"expected hostname_conflict error, got: {result}"
    assert (
        result.get("error") == "hostname_conflict"
    ), f"expected hostname_conflict error, got: {result}"
    # All required fields present
    assert "conflicting_machine_id" in result, "missing conflicting_machine_id in error response"
    assert "conflicting_status" in result, "missing conflicting_status in error response"
    assert "message" in result, "missing message in error response"

    # Verify values
    assert result["conflicting_machine_id"] == "detail-uuid-001", (
        f"expected conflicting_machine_id=detail-uuid-001, "
        f"got {result['conflicting_machine_id']}"
    )
    assert (
        result["conflicting_status"] == "idle"
    ), f"expected conflicting_status=idle, got {result['conflicting_status']}"
    # Verify message contains key info
    msg = result["message"]
    assert (
        "idle" in msg and "detail-uuid-001"[:8] in msg
    ), f"message missing status or machine_id: {msg}"
