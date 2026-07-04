"""Regression guard for issue #237 — PostgreSQL dict-row preservation.

When the workspace/governance modules migrated from bespoke ``_get_connection()``
(hand-rolled ``psycopg2.connect(cursor_factory=RealDictCursor)``) to the shared
``Database.connection()`` pool, the §2.1 review concern was: does the pooled
connection still yield RealDictCursor rows, so that every ``row["col"]`` access
in the migrated method bodies keeps working on PostgreSQL?

The preservation mechanism is ``PgConnectionWrapper``: it stashes the
``cursor_factory`` chosen at connection acquisition and re-injects it on every
``cursor()`` call, because ``ThreadedConnectionPool.putconn``/``getconn`` round
trips do not carry per-connection ``cursor_factory`` state. These tests pin that
contract so a future refactor of the wrapper cannot silently regress every
migrated module's dict-row access on PostgreSQL.

They need no live server — the wrapper is exercised against a stub connection.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2.extras import RealDictCursor  # noqa: E402

from app.repositories.database import PgConnectionWrapper  # noqa: E402


class TestPgConnectionWrapperPreservesDictCursor:
    """``conn.cursor()`` on a pooled PG connection must inject RealDictCursor."""

    def test_cursor_uses_configured_cursor_factory(self):
        """The wrapper re-injects the stashed cursor_factory on every cursor()."""
        raw = MagicMock()
        wrapper = PgConnectionWrapper(raw, cursor_factory=RealDictCursor)

        wrapper.cursor()

        raw.cursor.assert_called_once_with(cursor_factory=RealDictCursor)

    def test_explicit_none_still_resolves_to_configured_factory(self):
        """Calling cursor(cursor_factory=None) must not clear the configured factory.

        Migrated method bodies call ``conn.cursor()`` with no argument; this test
        documents that even an explicit ``None`` cannot defeat the injected
        RealDictCursor (the wrapper fills it in).
        """
        raw = MagicMock()
        wrapper = PgConnectionWrapper(raw, cursor_factory=RealDictCursor)

        wrapper.cursor(cursor_factory=None)

        raw.cursor.assert_called_once_with(cursor_factory=RealDictCursor)

    def test_caller_provided_factory_wins(self):
        """A caller asking for a specific factory is honoured (escape hatch)."""
        raw = MagicMock()
        wrapper = PgConnectionWrapper(raw, cursor_factory=RealDictCursor)

        class _Other:
            pass

        wrapper.cursor(cursor_factory=_Other)

        raw.cursor.assert_called_once_with(cursor_factory=_Other)

    def test_no_configured_factory_passes_none(self):
        """A wrapper built without a cursor_factory forwards None unchanged."""
        raw = MagicMock()
        wrapper = PgConnectionWrapper(raw)

        wrapper.cursor()

        raw.cursor.assert_called_once_with(cursor_factory=None)

    def test_connection_level_attrs_delegate_to_raw(self):
        """commit/rollback/execute (anything via __getattr__) reach the raw conn.

        Migrated bodies call ``conn.commit()`` / ``conn.rollback()`` directly on
        the yielded connection; the wrapper must forward these transparently.
        """
        raw = MagicMock()
        wrapper = PgConnectionWrapper(raw, cursor_factory=RealDictCursor)

        wrapper.commit()
        wrapper.rollback()

        raw.commit.assert_called_once()
        raw.rollback.assert_called_once()


@pytest.fixture(autouse=True)
def _force_sqlite(monkeypatch):
    """The end-to-end Database read below runs against SQLite."""
    import app.repositories.database as db_mod

    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)


class TestDatabaseConnectionReturnsDictStyleRows:
    """End-to-end: a connection from ``Database.connection()`` returns rows
    supporting ``row["col"]`` access — the SQLite mirror of the §2.1 contract.

    Migrated modules obtain their connections via ``Database.connection()``; the
    SQLite path sets ``row_factory = sqlite3.Row`` (the RealDictCursor
    equivalent), so every ``row["col"]`` access in a migrated method body works.
    Pinned here so a refactor of ``Database._get_sqlite_connection`` cannot
    silently drop the ``row_factory`` and break dict-style access.
    """

    def test_database_connection_row_supports_dict_access(self, tmp_path):
        from app.repositories.database import Database

        db_url = f"sqlite:///{tmp_path / 'dict_row.db'}"
        db = Database(db_url=db_url)

        with db.connection() as conn:
            conn.execute("CREATE TABLE t (name TEXT)")
            conn.execute("INSERT INTO t (name) VALUES (?)", ("DictRow",))
            conn.commit()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM t")
            row = cursor.fetchone()

        assert row["name"] == "DictRow"
