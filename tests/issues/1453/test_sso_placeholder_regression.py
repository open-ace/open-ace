"""Regression tests for SSO placeholder adaptation (Issue #1453).

The SSO write methods must run their SQL through ``Database.execute()`` so the
``?`` placeholders get rewritten to ``%s`` on PostgreSQL. Before this fix every
write method used a raw ``cursor.execute("... ? ...")`` and raised
``syntax error at or near ","`` on PostgreSQL, breaking all of SSO.

These tests mirror the ``test_retention.py`` pattern (issue #860): patch
``is_postgresql()`` and spy on the SQL the underlying cursor receives, so they
catch the regression in CI without a live PostgreSQL server. They use a *real*
``Database`` instance with only ``connection()`` mocked out, so ``execute()``
runs its real body — including ``adapt_sql(query)`` — and the spy captures the
fully-adapted SQL the driver would see.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import app.repositories.database as db_mod
from app.modules.sso.manager import SSOManager
from app.repositories.database import Database


def _make_manager_with_spy() -> tuple[SSOManager, list[str]]:
    """Build an SSOManager backed by a real Database with a mocked connection.

    ``Database.execute()`` runs its real body (calling ``adapt_sql(query)``),
    but ``connection()`` returns a mock so no DB is touched. The spy captures
    every SQL string handed to the underlying cursor — i.e. AFTER adaptation,
    exactly what the driver would receive.
    """
    db = Database(db_url="sqlite:///dummy")  # real Database, dialect controlled via is_postgresql
    captured: list[str] = []

    mock_conn = MagicMock()
    cursor = mock_conn.cursor.return_value
    cursor.execute.side_effect = lambda query, *args, **kwargs: captured.append(query)
    # execute() does `with self.connection() as conn:` then `conn.cursor()`.
    # The context manager's __enter__ must yield mock_conn itself.
    mock_connection_ctx = MagicMock()
    mock_connection_ctx.__enter__.return_value = mock_conn
    db.connection = MagicMock(return_value=mock_connection_ctx)  # type: ignore[method-assign]

    manager = SSOManager(db=db)
    return manager, captured


def test_register_provider_adapts_placeholders_for_postgres(monkeypatch):
    """INSERT ... ON CONFLICT for register_provider must use %s (not ?) on PG."""
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

    manager, captured = _make_manager_with_spy()
    manager.register_provider(
        name="google",
        provider_type="oauth2",
        client_id="cid",
        client_secret="csec",
        authorization_url="https://example.com/auth",
        token_url="https://example.com/token",
    )

    insert_sqls = [q for q in captured if "INSERT" in q.upper()]
    assert len(insert_sqls) == 1
    assert "%s" in insert_sqls[0]
    assert "?" not in insert_sqls[0]


def test_register_provider_keeps_sqlite_placeholders(monkeypatch):
    """Under SQLite the '?' placeholder is preserved (adapt_sql is a no-op)."""
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)

    manager, captured = _make_manager_with_spy()
    manager.register_provider(
        name="google",
        provider_type="oauth2",
        client_id="cid",
        client_secret="csec",
        authorization_url="https://example.com/auth",
        token_url="https://example.com/token",
    )

    insert_sqls = [q for q in captured if "INSERT" in q.upper()]
    assert len(insert_sqls) == 1
    assert "?" in insert_sqls[0]
    assert "%s" not in insert_sqls[0]


def test_disable_provider_adapts_placeholders_for_postgres(monkeypatch):
    """UPDATE in disable_provider must use %s (not ?) on PostgreSQL."""
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

    manager, captured = _make_manager_with_spy()
    manager.disable_provider("google")

    update_sqls = [q for q in captured if "UPDATE" in q.upper()]
    assert len(update_sqls) == 1
    assert "%s" in update_sqls[0]
    assert "?" not in update_sqls[0]


def test_cleanup_expired_sessions_adapts_placeholders_for_postgres(monkeypatch):
    """DELETE in cleanup_expired_sessions must use %s (not ?) on PostgreSQL."""
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

    manager, captured = _make_manager_with_spy()
    manager.cleanup_expired_sessions()

    delete_sqls = [q for q in captured if "DELETE" in q.upper()]
    assert len(delete_sqls) == 1
    assert "%s" in delete_sqls[0]
    assert "?" not in delete_sqls[0]


def test_store_auth_state_adapts_placeholders_for_postgres(monkeypatch):
    """INSERT in _store_auth_state must use %s (not ?) on PostgreSQL."""
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

    manager, captured = _make_manager_with_spy()
    manager._store_auth_state("state1", "ver1", "google", "nonce1")

    insert_sqls = [q for q in captured if "INSERT" in q.upper()]
    assert len(insert_sqls) == 1
    assert "%s" in insert_sqls[0]
    assert "?" not in insert_sqls[0]
