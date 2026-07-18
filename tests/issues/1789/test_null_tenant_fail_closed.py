"""TDD tests for the tenant-scope fail-closed fix (PR #1789 review findings).

These tests exercise the null-tenant non-admin path that the original PR
#1789 left as fail-open: a non-admin user whose ``tenant_id`` resolves to
None was treated as "global scope" instead of "deny". That allowed
cross-tenant read AND write (``update_session_fields`` /
``increment_session_usage`` have no owner check, so the tenant clause is
their sole authorization).

The tests must FAIL on unfixed main and PASS after the fail-closed fix.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from flask import Flask

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.session_manager import SessionManager
from app.repositories import database as db_mod
from app.repositories.schema_init import load_schema_from_file


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """SQLite database with the authoritative schema loaded."""
    # Force both the manager and the database helpers into SQLite mode so that
    # adapt_sql() leaves '?' placeholders intact in this Postgres-default env.
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    db_path = tmp_path / "tenant_boundaries.db"
    db_url = f"sqlite:///{db_path}"
    load_schema_from_file(db_url=db_url, dialect="sqlite")
    return db_path


def _session_manager(db_path) -> SessionManager:
    return SessionManager(db_path=str(db_path))


def _insert_user(db_path, user_id: int, username: str, tenant_id) -> None:
    """Insert a user directly via sqlite3 (bypasses adapt_sql placeholder rewrite)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO users (id, username, email, password_hash, role, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                f"{username}@example.com",
                "hash",
                "user",
                tenant_id,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── Manager-layer write path: tenant_id=None must NOT mutate cross-tenant ──


def test_update_session_fields_with_null_tenant_fails_closed(sqlite_db):
    """update_session_fields(tenant_id=None, require_tenant=True) must not mutate
    another tenant's row.

    Before the fix an empty tenant clause made the UPDATE match across all
    tenants, so a null-tenant caller hijacked any session by id. With
    ``require_tenant=True`` the manager now fails closed (matches nothing).
    """
    db_path = sqlite_db
    manager = _session_manager(db_path)

    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2, title="original")

    mutated = manager.update_session_fields(
        "tenant-two-session", {"title": "hijacked"}, tenant_id=None, require_tenant=True
    )
    assert mutated is False, "fail-open bug: null tenant mutated another tenant's session"

    session = manager.get_session("tenant-two-session", tenant_id=2)
    assert session is not None
    assert session.title == "original", "null-tenant write leaked across the tenant boundary"


def test_increment_session_usage_with_null_tenant_fails_closed(sqlite_db):
    """increment_session_usage(tenant_id=None, require_tenant=True) must not bump
    another tenant's counters."""
    db_path = sqlite_db
    manager = _session_manager(db_path)

    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2)

    bumped = manager.increment_session_usage(
        "tenant-two-session",
        request_delta=7,
        total_tokens_delta=777,
        tenant_id=None,
        require_tenant=True,
    )
    assert bumped is False, "fail-open bug: null tenant bumped another tenant's usage counters"

    session = manager.get_session("tenant-two-session", tenant_id=2)
    assert session is not None
    assert session.request_count == 0
    assert session.total_tokens == 0


def test_global_sentinel_still_allows_admin_global_write(sqlite_db):
    """An explicit global sentinel (system-admin intent) must still write globally."""
    db_path = sqlite_db
    manager = _session_manager(db_path)

    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2, title="original")

    mutated = manager.update_session_fields(
        "tenant-two-session",
        {"title": "admin override"},
        tenant_id=sm_mod.GLOBAL_TENANT_SENTINEL,
    )
    assert mutated is True, "explicit global sentinel should permit cross-tenant admin write"

    session = manager.get_session("tenant-two-session", tenant_id=2)
    assert session is not None
    assert session.title == "admin override"


# ── Route-layer: _session_lookup_tenant_id must deny, not go global ────────


def test_session_lookup_tenant_id_denies_null_tenant_non_admin():
    """A non-admin with no tenant_id must not get None (= global) scope."""
    from flask import Flask

    from app.routes.workspace import _session_lookup_tenant_id

    app = Flask(__name__)
    with app.test_request_context():
        from flask import g

        g.user = {"id": 99, "role": "user", "tenant_id": None}
        with pytest.raises(Exception):
            # Before the fix this returns None (global). After the fix it must
            # raise/abort so the request is denied instead of going global.
            _session_lookup_tenant_id()


def test_session_lookup_tenant_id_stays_global_for_admin():
    """A system admin legitimately keeps global scope (returns None)."""
    from flask import Flask

    from app.routes.workspace import _session_lookup_tenant_id

    app = Flask(__name__)
    with app.test_request_context():
        from flask import g

        g.user = {"id": 1, "role": "admin", "tenant_id": None}
        assert _session_lookup_tenant_id() is None


# ── session_access.check_session_access: null-tenant non-admin denied ─────


def test_check_session_access_denies_null_tenant_machine_admin(sqlite_db, monkeypatch):
    """A null-tenant non-admin who is a machine admin must NOT read another
    tenant's session via the machine-admin branch.

    Before the fix: the line-43 cross-tenant guard
    ``current_tenant_id not in (None, session.tenant_id)`` is False when
    ``current_tenant_id`` is None, so the check is skipped; ``get_session``
    with tenant_id=None returns the cross-tenant row; the machine-admin
    branch (which is NOT tenant-filtered) then grants access. After the fix
    a non-admin with no tenant fails closed before reaching that branch.
    """
    db_path = sqlite_db
    _insert_user(db_path, 10, "tenant2-owner", 2)
    _insert_user(db_path, 99, "null-tenant-user", None)

    manager = _session_manager(db_path)
    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2, user_id=10)

    from app.modules.workspace import session_access

    app = Flask(__name__)
    with app.test_request_context():
        from flask import g

        g.user = {"id": 99, "role": "user", "tenant_id": None}

        # Stub the remote session manager. get_session_status reports a machine
        # the null-tenant user "admin"s (so the machine-admin branch would
        # otherwise grant access); get_session(tenant_id=None) returns the
        # cross-tenant row before the fail-closed fix.
        class _StubRemoteMgr:
            def __init__(self):
                self._session_manager = manager
                self._data = {"session_id": "tenant-two-session", "machine_id": "M1"}

            def get_session_status(self, session_id):
                return self._data if session_id == "tenant-two-session" else None

        class _StubAgentMgr:
            def get_user_permission(self, machine_id, user_id):
                # Null-tenant user 99 is an "admin" of machine M1 — the unguarded
                # machine-admin branch would let them in before the fix.
                return "admin"

        monkeypatch.setattr(session_access, "get_remote_session_manager", lambda: _StubRemoteMgr())
        monkeypatch.setattr(session_access, "get_remote_agent_manager", lambda: _StubAgentMgr())

        _session, error = session_access.check_session_access("tenant-two-session")
        assert error is not None, (
            "null-tenant machine-admin must be denied (fail closed), not admitted via the "
            "untenant-scoped machine-admin branch"
        )
        status_code = error[1]
        assert status_code == 403, f"expected 403, got {status_code}"
