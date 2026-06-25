"""Tests for schema single-source-of-truth (#1273).

Guards against the "shadow schema" drift that plagued SessionManager and
ensure_all_tables: both had hand-maintained CREATE TABLE statements that lost
columns present in the authoritative schema.sql (project_id, project_path,
request_count, ...). These tests assert the new load_schema_from_file() path
builds the FULL authoritative schema, and that re-running it is idempotent.
"""

import sqlite3
from pathlib import Path

import pytest

from app.repositories.schema_init import (
    _iter_pg_statements,
    _make_idempotent,
    load_schema_from_file,
    schema_file_for_dialect,
)


class TestIterPgStatements:
    """Lock in statement splitting — esp. that a multi-line MATERIALIZED VIEW
    is emitted as a single statement (not fragmented)."""

    def test_materialized_view_single_statement(self):
        sql = (
            "CREATE TABLE foo (id INTEGER);\n"
            "CREATE MATERIALIZED VIEW session_stats\n"
            "AS SELECT 1\n"
            "WITH NO DATA;\n"
            "CREATE SEQUENCE bar_seq;\n"
        )
        stmts = list(_iter_pg_statements(sql))
        assert len(stmts) == 3
        assert "MATERIALIZED VIEW" in stmts[1]
        assert "WITH NO DATA" in stmts[1]  # multi-line view kept together

    def test_skips_comments_and_blanks(self):
        sql = "-- a comment\n\nCREATE TABLE foo (id INTEGER);\n"
        stmts = list(_iter_pg_statements(sql))
        assert len(stmts) == 1
        assert "CREATE TABLE" in stmts[0]

    def test_trailing_statement_without_semicolon(self):
        sql = "CREATE TABLE foo (id INTEGER);\nCREATE TABLE bar (id INTEGER)"
        stmts = list(_iter_pg_statements(sql))
        assert len(stmts) == 2

    def test_real_schema_postgres_no_fragmentation(self):
        """The real schema-postgres.sql splits into a sane number of statements,
        and the MATERIALIZED VIEW isn't broken across multiple yields."""
        sql = schema_file_for_dialect("postgresql").read_text(encoding="utf-8")
        stmts = list(_iter_pg_statements(sql))
        # Should be many statements (54 tables + sequences + indexes + view).
        assert len(stmts) > 100
        mv_stmts = [s for s in stmts if "MATERIALIZED VIEW" in s]
        if mv_stmts:  # the file contains one
            assert len(mv_stmts) == 1
            assert "WITH NO DATA" in mv_stmts[0]


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _all_tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


class TestMakeIdempotent:
    def test_create_table_gets_if_not_exists(self):
        out = _make_idempotent("CREATE TABLE foo (id INTEGER);\n")
        assert "CREATE TABLE IF NOT EXISTS foo" in out

    def test_create_index_gets_if_not_exists(self):
        out = _make_idempotent("CREATE INDEX idx ON foo(id);\n")
        assert "CREATE INDEX IF NOT EXISTS idx" in out

    def test_create_unique_index_gets_if_not_exists(self):
        out = _make_idempotent("CREATE UNIQUE INDEX idx ON foo(id);\n")
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx" in out

    def test_already_idempotent_unchanged(self):
        sql = "CREATE TABLE IF NOT EXISTS foo (id INTEGER);\n"
        assert _make_idempotent(sql) == sql

    def test_non_create_lines_untouched(self):
        sql = "INSERT INTO foo VALUES (1);\n-- a comment\n"
        assert _make_idempotent(sql) == sql


class TestLoadSchemaFromFile:
    def test_builds_all_authoritative_tables(self, tmp_path):
        """load_schema_from_file creates all tables from schema-sqlite.sql."""
        db_file = tmp_path / "test.db"
        load_schema_from_file(db_url=f"sqlite:///{db_file}", dialect="sqlite")
        conn = sqlite3.connect(str(db_file))
        tables = _all_tables(conn)
        conn.close()
        # Spot-check a representative set across modules.
        for required in (
            "agent_sessions",
            "session_messages",
            "autonomous_workflows",
            "users",
            "workflow_milestones",
        ):
            assert required in tables, f"{required} table missing"

    def test_agent_sessions_has_drifted_columns(self, tmp_path):
        """The columns that were lost to shadow-schema drift are present."""
        db_file = tmp_path / "test.db"
        load_schema_from_file(db_url=f"sqlite:///{db_file}", dialect="sqlite")
        conn = sqlite3.connect(str(db_file))
        cols = _table_columns(conn, "agent_sessions")
        conn.close()
        for col in ("project_id", "project_path", "request_count", "cli_session_id"):
            assert col in cols, f"agent_sessions.{col} missing (shadow-schema drift)"

    def test_idempotent_re_run(self, tmp_path):
        """Re-running on an existing DB is a no-op (CREATE IF NOT EXISTS)."""
        db_file = tmp_path / "test.db"
        load_schema_from_file(db_url=f"sqlite:///{db_file}", dialect="sqlite")
        # Second run must not raise even though all tables exist.
        load_schema_from_file(db_url=f"sqlite:///{db_file}", dialect="sqlite")
        conn = sqlite3.connect(str(db_file))
        assert "agent_sessions" in _all_tables(conn)
        conn.close()


class TestEnsureTablesMatchesSchema:
    """SessionManager._ensure_tables() must produce the same columns as schema.sql
    — the core anti-drift guarantee of #1273."""

    def test_session_manager_columns_match_schema_sql(self, tmp_path, monkeypatch):
        import app.modules.workspace.session_manager as sm_mod
        from app.modules.workspace.session_manager import SessionManager

        monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
        db_file = tmp_path / "sm.db"
        sm = SessionManager(db_path=str(db_file))
        sm._ensure_tables()

        conn = sqlite3.connect(str(db_file))
        sm_cols = _table_columns(conn, "agent_sessions")
        conn.close()

        # Reference: columns directly from schema-sqlite.sql.
        schema_sql = schema_file_for_dialect("sqlite").read_text(encoding="utf-8")
        # Parse column names from the agent_sessions CREATE TABLE block.
        schema_cols = set()
        in_block = False
        for line in schema_sql.splitlines():
            stripped = line.strip()
            if stripped.startswith("CREATE TABLE agent_sessions"):
                in_block = True
                continue
            if in_block:
                if stripped.startswith(")"):
                    break
                # column line: " name type ..."
                first = stripped.split()[0] if stripped.split() else ""
                if first and not first.startswith("PRIMARY") and not first.startswith("FOREIGN"):
                    schema_cols.add(first)

        # _ensure_tables columns must be a superset of schema.sql columns.
        missing = schema_cols - sm_cols
        assert not missing, f"_ensure_tables() is missing columns vs schema.sql: {missing}"


class TestSchemaFileForDialect:
    def test_sqlite_path_exists(self):
        assert schema_file_for_dialect("sqlite").exists()

    def test_postgres_path_exists(self):
        assert schema_file_for_dialect("postgresql").exists()

    def test_unknown_dialect_raises(self):
        with pytest.raises(ValueError):
            schema_file_for_dialect("mysql")
