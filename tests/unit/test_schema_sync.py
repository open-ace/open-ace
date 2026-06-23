#!/usr/bin/env python3
"""Unit tests for schema sync helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.shared import schema_sync


def test_compare_sqlite_snapshots_matches_identical_schema():
    """Identical schemas should not report drift."""
    sql = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL
    );
    CREATE INDEX idx_users_username ON users (username);
    """

    left = schema_sync.sqlite_snapshot_from_sql(sql)
    right = schema_sync.sqlite_snapshot_from_sql(sql)
    diff = schema_sync.compare_sqlite_snapshots(left, right)

    assert not diff.has_drift()


def test_compare_sqlite_snapshots_detects_table_column_and_index_drift():
    """Different schemas should report structural drift."""
    actual = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL
        );
        CREATE INDEX idx_users_username ON users (username);
        """
    )
    expected = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT
        );
        CREATE TABLE tenants (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE INDEX idx_users_email ON users (email);
        """
    )

    diff = schema_sync.compare_sqlite_snapshots(actual, expected)

    assert diff.has_drift()
    assert diff.tables_only_in_expected == ["tenants"]
    assert diff.indexes_only_in_actual == ["idx_users_username"]
    assert diff.indexes_only_in_expected == ["idx_users_email"]
    assert "users" in diff.column_diffs


def test_compare_postgres_schema_text_normalizes_line_endings():
    """PostgreSQL schema comparison should ignore CRLF/LF differences."""
    expected = "CREATE TABLE users (\n    id integer\n);\n"
    actual = "CREATE TABLE users (\r\n    id integer\r\n);\r\n"

    diff = schema_sync.compare_postgres_schema_text(actual, expected)

    assert diff == []


def test_compare_sqlite_snapshots_ignores_pk_notnull_and_boolean_default_noise():
    """SQLite PK/nullability quirks and boolean literals should not cause drift."""
    actual = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_active INTEGER DEFAULT 0
        );
        """
    )
    expected = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            is_active BOOLEAN DEFAULT false
        );
        """
    )

    diff = schema_sync.compare_sqlite_snapshots(actual, expected)

    assert not diff.has_drift()


def test_compare_sqlite_snapshots_matches_equivalent_indexes_with_different_names():
    """Equivalent index semantics should match even when names differ."""
    actual = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            host_name TEXT NOT NULL,
            UNIQUE (date, tool_name, host_name)
        );
        CREATE INDEX idx_usage_date ON daily_usage (date);
        """
    )
    expected = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            host_name TEXT NOT NULL
        );
        CREATE UNIQUE INDEX uq_daily_usage_date_tool_host
        ON daily_usage (date, tool_name, host_name);
        CREATE INDEX idx_daily_usage_date ON daily_usage (date);
        """
    )

    diff = schema_sync.compare_sqlite_snapshots(actual, expected)

    assert not diff.has_drift()


def test_compare_sqlite_snapshots_ignores_column_order_and_where_formatting_noise():
    """Column order and equivalent partial-index predicates should not drift."""
    actual = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE daily_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            agent_session_id TEXT
        );
        CREATE INDEX idx_messages_session
        ON daily_messages (agent_session_id)
        WHERE ((agent_session_id IS NOT NULL));
        """
    )
    expected = schema_sync.sqlite_snapshot_from_sql(
        """
        CREATE TABLE daily_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_session_id TEXT,
            conversation_id TEXT
        );
        CREATE INDEX idx_messages_session
        ON daily_messages (agent_session_id)
        WHERE agent_session_id IS NOT NULL;
        """
    )

    diff = schema_sync.compare_sqlite_snapshots(actual, expected)

    assert not diff.has_drift()
