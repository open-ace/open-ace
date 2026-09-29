"""Migration 20260926_001: drop duplicate NULL-sender daily_stats rows (#3424)."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.issue(3424)]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260926_001_dedupe_daily_stats_null_sender.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("dedupe_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(conn) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        _load_migration().upgrade()


_ROWS = [
    # (date, tool, host, sender, tokens, updated_at)
    ("2025-06-15", "qwen", "h1", None, 10, "2025-06-15 01:00:00"),
    ("2025-06-15", "qwen", "h1", None, 30, "2025-06-15 03:00:00"),  # newest copy
    ("2025-06-15", "qwen", "h1", None, 20, "2025-06-15 02:00:00"),
    ("2025-06-15", "qwen", "h2", None, 5, "2025-06-15 01:00:00"),  # single, kept
    ("2025-06-15", "qwen", "h1", "alice", 7, "2025-06-15 01:00:00"),  # non-NULL, kept
]


def _seed(conn) -> None:
    for date, tool, host, sender, tokens, updated_at in _ROWS:
        conn.execute(
            sa.text(
                "INSERT INTO daily_stats (date, tool_name, host_name, sender_name,"
                " total_tokens, total_input_tokens, total_output_tokens, message_count,"
                " updated_at) VALUES (:d, :t, :h, :s, :tok, 0, 0, 1, :u)"
            ),
            {"d": date, "t": tool, "h": host, "s": sender, "tok": tokens, "u": updated_at},
        )


def _remaining(conn) -> list[tuple]:
    rows = conn.execute(
        sa.text(
            "SELECT host_name, sender_name, total_tokens FROM daily_stats"
            " ORDER BY host_name, sender_name, total_tokens"
        )
    ).fetchall()
    return sorted((r[0], r[1] or "", r[2]) for r in rows)


_EXPECTED = [("h1", "", 30), ("h1", "alice", 7), ("h2", "", 5)]


def test_dedupe_keeps_newest_null_sender_row_sqlite():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("""
                CREATE TABLE daily_stats (
                    date VARCHAR(10) NOT NULL, tool_name VARCHAR(50) NOT NULL,
                    host_name VARCHAR(100) NOT NULL, sender_name VARCHAR(100),
                    total_tokens INTEGER NOT NULL, total_input_tokens INTEGER NOT NULL,
                    total_output_tokens INTEGER NOT NULL, message_count INTEGER NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    UNIQUE (date, tool_name, host_name, sender_name)
                )
                """))
        _seed(conn)
        _upgrade(conn)
        assert _remaining(conn) == _EXPECTED
        # Idempotent: a second run deletes nothing.
        _upgrade(conn)
        assert _remaining(conn) == _EXPECTED
