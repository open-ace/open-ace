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


def test_sqlite_upgrade_rebuilds_claude_summary_with_distinct_day_count(tmp_path):
    """Claude summary rebuild should count distinct days across alias history."""
    db_path = tmp_path / "migration_039_claude_summary.db"

    _run_alembic(db_path, "038_normalize_tool_names")

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
            "claude",
            "build-host",
            "msg-claude",
            "assistant",
            "claude message",
            100,
            70,
            30,
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
            "2026-05-02",
            "claude-code",
            "build-host",
            "msg-claude-code",
            "assistant",
            "claude-code message",
            200,
            120,
            80,
            "2026-05-02 03:00:00",
            "agent",
        ),
    )
    conn.execute(
        """
        INSERT INTO usage_summary
        (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
         total_input_tokens, total_output_tokens, first_date, last_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("claude", "build-host", 1, 100, 100, 1, 70, 30, "2026-05-01", "2026-05-01"),
    )
    conn.execute(
        """
        INSERT INTO usage_summary
        (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
         total_input_tokens, total_output_tokens, first_date, last_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("claude-code", "build-host", 1, 200, 200, 1, 120, 80, "2026-05-02", "2026-05-02"),
    )
    conn.execute(
        """
        INSERT INTO usage_summary
        (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
         total_input_tokens, total_output_tokens, first_date, last_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("claude", "", 1, 100, 100, 1, 70, 30, "2026-05-01", "2026-05-01"),
    )
    conn.execute(
        """
        INSERT INTO usage_summary
        (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
         total_input_tokens, total_output_tokens, first_date, last_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("claude-code", "", 1, 200, 200, 1, 120, 80, "2026-05-02", "2026-05-02"),
    )
    conn.commit()
    conn.close()

    _run_alembic(db_path, "039_normalize_derived_tables")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    usage_summary = conn.execute(
        """
        SELECT tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
               total_input_tokens, total_output_tokens, first_date, last_date
        FROM usage_summary
        WHERE tool_name = ?
        ORDER BY host_name
        """,
        ("claude",),
    ).fetchall()
    assert [dict(row) for row in usage_summary] == [
        {
            "tool_name": "claude",
            "host_name": "",
            "days_count": 2,
            "total_tokens": 300,
            "avg_tokens": 150,
            "total_requests": 2,
            "total_input_tokens": 190,
            "total_output_tokens": 110,
            "first_date": "2026-05-01",
            "last_date": "2026-05-02",
        },
        {
            "tool_name": "claude",
            "host_name": "build-host",
            "days_count": 2,
            "total_tokens": 300,
            "avg_tokens": 150,
            "total_requests": 2,
            "total_input_tokens": 190,
            "total_output_tokens": 110,
            "first_date": "2026-05-01",
            "last_date": "2026-05-02",
        },
    ]

    alias_counts = conn.execute(
        """
        SELECT COUNT(*)
        FROM usage_summary
        WHERE tool_name = 'claude-code'
        """
    ).fetchone()[0]
    assert alias_counts == 0
    conn.close()
