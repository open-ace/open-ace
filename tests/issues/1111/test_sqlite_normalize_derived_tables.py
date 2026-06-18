#!/usr/bin/env python3
"""Regression test for SQLite alias collisions in migration 039."""

import os
import sqlite3
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


def _run_alembic(db_path, revision):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", revision],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def test_sqlite_upgrade_merges_qwen_alias_rows_before_normalization(tmp_path):
    """Upgrade chain should merge colliding qwen alias rows instead of failing."""
    db_path = tmp_path / "migration_039_collision.db"

    _run_alembic(db_path, "017_add_usage_summary")

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, content, tokens_used,
         input_tokens, output_tokens, timestamp, sender_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-01",
            "qwen-code",
            "build-host",
            "msg-qwen-code",
            "assistant",
            "first message",
            100,
            60,
            40,
            "2026-05-01 03:00:00",
            "agent",
        ),
    )
    conn.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, content, tokens_used,
         input_tokens, output_tokens, timestamp, sender_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-01",
            "qwen-code-cli",
            "build-host",
            "msg-qwen-code-cli",
            "assistant",
            "second message",
            150,
            90,
            60,
            "2026-05-01 03:30:00",
            "agent",
        ),
    )
    conn.commit()
    conn.close()

    _run_alembic(db_path, "head")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    daily_stats = conn.execute(
        """
        SELECT tool_name, total_tokens, total_input_tokens, total_output_tokens, message_count
        FROM daily_stats
        WHERE date = ? AND host_name = ? AND sender_name = ?
        """,
        ("2026-05-01", "build-host", "agent"),
    ).fetchall()
    assert len(daily_stats) == 1
    assert dict(daily_stats[0]) == {
        "tool_name": "qwen",
        "total_tokens": 250,
        "total_input_tokens": 150,
        "total_output_tokens": 100,
        "message_count": 2,
    }

    hourly_stats = conn.execute(
        """
        SELECT tool_name, hour, total_tokens, total_input_tokens, total_output_tokens, message_count
        FROM hourly_stats
        WHERE date = ? AND host_name = ?
        """,
        ("2026-05-01", "build-host"),
    ).fetchall()
    assert len(hourly_stats) == 1
    assert dict(hourly_stats[0]) == {
        "tool_name": "qwen",
        "hour": 11,
        "total_tokens": 250,
        "total_input_tokens": 150,
        "total_output_tokens": 100,
        "message_count": 2,
    }

    usage_summary = conn.execute(
        """
        SELECT tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
               total_input_tokens, total_output_tokens, first_date, last_date
        FROM usage_summary
        WHERE tool_name = ?
        ORDER BY host_name
        """,
        ("qwen",),
    ).fetchall()
    assert [dict(row) for row in usage_summary] == [
        {
            "tool_name": "qwen",
            "host_name": "",
            "days_count": 1,
            "total_tokens": 250,
            "avg_tokens": 250,
            "total_requests": 2,
            "total_input_tokens": 150,
            "total_output_tokens": 100,
            "first_date": "2026-05-01",
            "last_date": "2026-05-01",
        },
        {
            "tool_name": "qwen",
            "host_name": "build-host",
            "days_count": 1,
            "total_tokens": 250,
            "avg_tokens": 250,
            "total_requests": 2,
            "total_input_tokens": 150,
            "total_output_tokens": 100,
            "first_date": "2026-05-01",
            "last_date": "2026-05-01",
        },
    ]

    alias_counts = conn.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT tool_name FROM daily_stats
            UNION ALL
            SELECT tool_name FROM hourly_stats
            UNION ALL
            SELECT tool_name FROM usage_summary
        )
        WHERE tool_name IN ('qwen-code', 'qwen-code-cli')
        """
    ).fetchone()[0]
    assert alias_counts == 0
    conn.close()
