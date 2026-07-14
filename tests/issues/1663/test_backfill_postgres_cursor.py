"""Regression tests for the PostgreSQL path of schema_init helpers (#1663).

The column-backfill helpers added in #1663 (``_table_exists``,
``_column_exists``, ``_backfill_missing_columns``) originally called
``conn.execute(...)`` directly. That works for sqlite3 (its Connection exposes
``execute``) but NOT for psycopg2: a psycopg2 connection has no ``.execute()``
attribute — only cursors do. On PostgreSQL the dev service crashed at startup:

    AttributeError: 'psycopg2.extensions.connection' object has no attribute 'execute'

These tests exercise the exact object shape that triggered the bug — a
connection with ``.cursor()`` but NO ``.execute()`` (wrapped in the real
``PgConnectionWrapper``) — so a revert to ``conn.execute()`` fails loudly.
"""

import pytest

from app.repositories.database import PgConnectionWrapper
from app.repositories.schema_init import _backfill_missing_columns, _column_exists, _table_exists


class _FakePsycopg2Cursor:
    """Records executed SQL into a shared list and answers the two
    information_schema introspection queries the helpers issue."""

    def __init__(self, tables, executed):
        # tables: {table_name: set(column_names)} present in the "database".
        self._tables = tables
        self._executed = executed
        self._fetch = None

    def execute(self, sql, params=None):
        sql = sql.strip()
        self._executed.append(sql)
        lower = sql.lower()
        if "information_schema.tables" in lower:
            # _table_exists: WHERE table_name = %s
            table = params[0] if params else None
            self._fetch = [(1,)] if table in self._tables else None
        elif "information_schema.columns" in lower:
            # _column_exists: WHERE table_name = %s AND column_name = %s
            table, column = params
            self._fetch = [(1,)] if column in self._tables.get(table, set()) else None
        else:
            # ALTER TABLE ... ADD COLUMN (or anything else): no row result.
            self._fetch = None

    def fetchone(self):
        return self._fetch

    def fetchall(self):
        return self._fetch or []

    def close(self):
        pass


class _FakePsycopg2Conn:
    """Minimal psycopg2 connection stand-in.

    It deliberately exposes NO ``execute`` attribute — exactly like a real
    psycopg2 connection — so going through ``PgConnectionWrapper`` (whose
    ``__getattr__`` delegates to this object) reproduces the AttributeError if
    a helper ever calls ``conn.execute(...)`` again.
    """

    def __init__(self, tables):
        self._tables = tables
        self.executed = []  # shared across every cursor created here

    def cursor(self, cursor_factory=None):
        return _FakePsycopg2Cursor(self._tables, self.executed)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _pg_conn(tables):
    """A PgConnectionWrapper around a cursor-only (psycopg2-shaped) fake."""
    return PgConnectionWrapper(_FakePsycopg2Conn(tables))


def _altered_columns(executed_sql):
    """Extract the column names from recorded ALTER TABLE ... ADD COLUMN stmts."""
    cols = []
    for stmt in executed_sql:
        if stmt.upper().startswith("ALTER TABLE") and "ADD COLUMN" in stmt.upper():
            # "ALTER TABLE <t> ADD COLUMN <col> <type> [DEFAULT ...]"
            after = stmt.upper().split("ADD COLUMN", 1)[1].strip().split()
            if after:
                cols.append(after[0].lower())
    return cols


class TestTableExistsPostgres:
    def test_present_and_absent_tables(self):
        conn = _pg_conn({"agent_sessions": {"id"}})
        # Must NOT raise AttributeError (the #1663 regression on psycopg2).
        assert _table_exists(conn, "agent_sessions", "postgresql") is True
        assert _table_exists(conn, "does_not_exist", "postgresql") is False


class TestColumnExistsPostgres:
    def test_present_and_absent_columns(self):
        conn = _pg_conn({"agent_sessions": {"id", "project_id"}})
        assert _column_exists(conn, "agent_sessions", "project_id", "postgresql") is True
        assert _column_exists(conn, "agent_sessions", "request_count", "postgresql") is False


class TestBackfillMissingColumnsPostgres:
    def test_adds_only_missing_columns(self):
        # agent_sessions exists with project_path already present; the other
        # backfill columns are missing and must be ALTERed in.
        conn = _pg_conn({"agent_sessions": {"id", "project_path"}})
        _backfill_missing_columns(conn, "postgresql")

        altered = _altered_columns(conn._conn.executed)
        # Missing ones are added ...
        assert "project_id" in altered
        assert "request_count" in altered
        # ... the already-present one is skipped (no duplicate ALTER).
        assert "project_path" not in altered

    def test_skips_when_table_absent(self):
        # Neither known table present: no introspection ALTERs, no crash.
        conn = _pg_conn({})
        _backfill_missing_columns(conn, "postgresql")
        assert _altered_columns(conn._conn.executed) == []

    def test_does_not_call_conn_execute(self):
        """The defining guard: a psycopg2-shaped connection (no .execute())
        must not blow up. Reverting to conn.execute() makes this raise."""
        conn = _pg_conn({"agent_sessions": {"id"}})
        # If any helper used conn.execute(), this would raise AttributeError.
        _backfill_missing_columns(conn, "postgresql")
        # Sanity: at least the table-existence introspection ran via cursor.
        assert any("information_schema" in s.lower() for s in conn._conn.executed)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
