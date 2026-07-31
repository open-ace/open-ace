"""Tests for tenant column startup assertion (Issue #1832 F8).

``SessionManager._ensure_tables`` ends with a read-only introspection that
fails fast when ``agent_sessions`` / ``session_messages`` lack the
``tenant_id`` column. Without it, ``_tenant_scope_condition`` silently
degrades — reads drop the tenant clause and writes (``require_tenant=True``)
fall through to ``"", []`` (no clause), i.e. a cross-tenant write. The
assertion turns that silent weakening into a loud startup error.

These tests also pin that the authoritative schema + back-fill keep the
assertion from false-firing on fresh and legacy DBs.
"""

import sqlite3

import pytest

from app.modules.workspace.session_manager import SessionManager

_LEGACY_AGENT_SESSIONS = """
CREATE TABLE agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    session_type TEXT DEFAULT 'chat',
    title TEXT,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost',
    user_id INTEGER,
    status TEXT DEFAULT 'active',
    context TEXT,
    settings TEXT,
    total_tokens INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    model TEXT,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP
)
"""

_LEGACY_SESSION_MESSAGES = """
CREATE TABLE session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tokens_used INTEGER DEFAULT 0,
    model TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT
)
"""


def _columns(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1] for row in rows}


def test_ensure_tables_passes_on_fresh_db(tmp_path, monkeypatch):
    """A fresh DB built by _ensure_tables always has tenant_id → no false fire."""
    monkeypatch.setattr("app.modules.workspace.session_manager.is_postgresql", lambda: False)
    db_path = tmp_path / "fresh.db"
    sm = SessionManager(db_path=str(db_path))
    sm._ensure_tables()  # must not raise

    assert "tenant_id" in _columns(db_path, "agent_sessions")
    assert "tenant_id" in _columns(db_path, "session_messages")


def test_ensure_tables_backfills_tenant_id_on_legacy_db(tmp_path, monkeypatch):
    """A pre-tenant legacy DB gets tenant_id back-filled, so the assertion holds."""
    monkeypatch.setattr("app.modules.workspace.session_manager.is_postgresql", lambda: False)
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_LEGACY_AGENT_SESSIONS)
    conn.execute(_LEGACY_SESSION_MESSAGES)
    conn.commit()
    conn.close()

    # Legacy tables start without tenant_id.
    assert "tenant_id" not in _columns(db_path, "agent_sessions")
    assert "tenant_id" not in _columns(db_path, "session_messages")

    sm = SessionManager(db_path=str(db_path))
    sm._ensure_tables()  # back-fills tenant_id, then asserts → must not raise

    assert "tenant_id" in _columns(db_path, "agent_sessions")
    assert "tenant_id" in _columns(db_path, "session_messages")


def test_ensure_tables_fails_fast_when_tenant_column_missing(tmp_path, monkeypatch):
    """Genuine drift (column absent after ensure) surfaces as a RuntimeError.

    Simulates a DB where tenant_id cannot be resolved — the back-fill ran but
    the column is still reported missing (real schema drift). The assertion
    must fail fast rather than silently weakening tenant isolation.
    """
    monkeypatch.setattr("app.modules.workspace.session_manager.is_postgresql", lambda: False)
    db_path = tmp_path / "drift.db"
    sm = SessionManager(db_path=str(db_path))
    sm._ensure_tables()  # build the schema first

    # Simulate drift: report tenant_id as missing regardless of reality.
    real_column_exists = SessionManager._column_exists

    @staticmethod
    def fake_column_exists(cursor, table, column):  # type: ignore[no-untyped-def]
        if column == "tenant_id":
            return False
        return real_column_exists(cursor, table, column)

    monkeypatch.setattr(SessionManager, "_column_exists", fake_column_exists)

    with pytest.raises(RuntimeError, match="tenant_id"):
        sm._ensure_tables()
