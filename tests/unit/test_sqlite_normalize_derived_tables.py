#!/usr/bin/env python3
"""Regression tests for qwen tool-name alias handling (Issue #1111).

The original regression lived in the retired Alembic chain
(038_normalize_tool_names / 039_normalize_derived_tables), which merged
qwen/qwen-code/qwen-code-cli rows during database upgrade. That chain was
folded into the baseline_2026_06_23 lineage and later retired outright
(#1688); alias unification now happens at the repository boundary —
normalize_tool_name at write time, and UsageRepository.get_summary_by_tool
merges alias rows at read time with a true DISTINCT day count. These tests
pin that current contract (#2457 realignment).
"""

import sqlite3
from datetime import datetime

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1111)]

SCHEMA_SQL = """
CREATE TABLE daily_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    tool_name TEXT NOT NULL DEFAULT 'test',
    host_name TEXT DEFAULT 'localhost',
    message_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    sender_name TEXT,
    model TEXT,
    message_source TEXT,
    agent_session_id TEXT,
    user_id INTEGER
);
"""


@pytest.fixture
def usage_repo(tmp_path, monkeypatch):
    """Temp SQLite DB with daily_messages, repository bound to it."""
    from app.repositories.database import Database
    from app.repositories.usage_repo import UsageRepository

    # Query shape follows the GLOBAL backend flag, not the injected
    # instance — pin it to the backend the connection actually opens.
    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

    db_path = str(tmp_path / "issue1111.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    return UsageRepository(db=Database(db_url=f"sqlite:///{db_path}"))


def _insert_message(repo, *, date, tool_name, tokens, role="assistant"):
    repo.db.execute(
        """
        INSERT INTO daily_messages
        (date, role, tokens_used, input_tokens, output_tokens, tool_name, host_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (date, role, tokens, tokens, 0, tool_name, "build-host"),
    )


def test_summary_by_tool_merges_qwen_alias_rows(usage_repo):
    """Alias rows (qwen/qwen-code/qwen-code-cli) must not split the summary.

    The retired migration chain merged these rows during upgrade; the
    current boundary is get_summary_by_tool, which normalizes each grouped
    row and merges same-tool results at read time.
    """
    day = datetime.now().strftime("%Y-%m-%d")

    _insert_message(usage_repo, date=day, tool_name="qwen", tokens=100)
    _insert_message(usage_repo, date=day, tool_name="qwen-code", tokens=200)
    _insert_message(usage_repo, date=day, tool_name="qwen-code-cli", tokens=300)
    _insert_message(usage_repo, date=day, tool_name="claude-code", tokens=50)

    summary = usage_repo.get_summary_by_tool()

    tools = set(summary.keys())
    assert "qwen" in tools, f"normalized qwen entry missing: {sorted(tools)}"
    assert "qwen-code" not in tools and "qwen-code-cli" not in tools

    qwen = summary.get("qwen")
    assert qwen is not None, f"summary shape changed: {summary}"
    assert qwen["total_tokens"] == 600, f"alias rows not merged: {qwen}"
    assert qwen["total_requests"] == 3, f"alias requests not merged: {qwen}"


def test_summary_by_tool_counts_distinct_days_across_aliases(usage_repo):
    """Same-day alias rows must not inflate the summary's day count.

    Migration 038 used to rebuild summaries so claude's day count stayed a
    true DISTINCT; the read-boundary merge keeps that property — alias rows
    landing on the same dates report the shared distinct-date count.
    """
    day1 = "2026-05-01"
    day2 = "2026-05-02"

    _insert_message(usage_repo, date=day1, tool_name="qwen", tokens=100)
    _insert_message(usage_repo, date=day2, tool_name="qwen", tokens=100)
    _insert_message(usage_repo, date=day1, tool_name="qwen-code", tokens=200)
    _insert_message(usage_repo, date=day2, tool_name="qwen-code-cli", tokens=300)

    summary = usage_repo.get_summary_by_tool()

    qwen = summary.get("qwen")
    assert qwen is not None, f"summary shape changed: {summary}"
    assert qwen["days_count"] == 2, f"same-date alias rows inflated the distinct day count: {qwen}"
    assert qwen["total_tokens"] == 700
    assert qwen["first_date"] == day1 and qwen["last_date"] == day2


def test_summary_by_tool_avg_tokens_spans_aliases(usage_repo):
    """avg_tokens must average the whole alias family, not one alias's rows.

    The span recompute (Issue #1111) rebuilt days_count/first_date/last_date
    across the family, but avg_tokens kept the highest-total alias's grouped
    AVG — a small alias merging under a dominant one reported the dominant
    average instead of the family-wide one.
    """
    day1 = "2026-05-01"
    day2 = "2026-05-02"

    # qwen-code carries the largest total (its grouped row is read first);
    # qwen contributes one small row to the same family.
    _insert_message(usage_repo, date=day1, tool_name="qwen-code", tokens=1000)
    _insert_message(usage_repo, date=day2, tool_name="qwen-code", tokens=1000)
    _insert_message(usage_repo, date=day1, tool_name="qwen", tokens=10)

    summary = usage_repo.get_summary_by_tool()

    qwen = summary.get("qwen")
    assert qwen is not None, f"summary shape changed: {summary}"
    # Family-wide average over all three rows, not the single-alias AVG(1000).
    assert qwen["avg_tokens"] == round(
        (1000 + 1000 + 10) / 3, 2
    ), f"avg_tokens did not span the alias family: {qwen}"
