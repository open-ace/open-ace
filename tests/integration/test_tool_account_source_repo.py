#!/usr/bin/env python3
"""Issue #1829 — F3 (repo layer): tool-account classification de-substringed.

Previously ``get_unmapped_tool_accounts`` classified tool_type by brittle
sender_name substrings (``-dingtalk`` etc.). Real DingTalk userids don't follow
a ``-dingtalk`` convention, so that heuristic rarely matched. F3 (repo):
``get_unmapped_tool_accounts`` now resolves each sender's ``message_source``
(the source of its most recent row) via a correlated subquery, deterministically
across SQLite/PostgreSQL.

These tests run a real SQLite database through the app ``Database`` /
``UserToolAccountRepository`` stack. The route-layer classification built on
top of this lives in tests/unit/test_tool_account_source_classification.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.repositories.database import Database
from app.repositories.user_tool_account_repo import UserToolAccountRepository

pytestmark = [pytest.mark.regression, pytest.mark.issue(1829)]

_SCHEMA = """
CREATE TABLE daily_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost' NOT NULL,
    message_id TEXT NOT NULL,
    parent_id TEXT,
    role TEXT NOT NULL,
    content TEXT,
    full_entry TEXT,
    tokens_used INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    model TEXT,
    "timestamp" TIMESTAMP,
    sender_id TEXT,
    sender_name TEXT,
    message_source TEXT,
    feishu_conversation_id TEXT,
    group_subject TEXT,
    is_group_chat INTEGER,
    user_id INTEGER
);
CREATE TABLE user_tool_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    tool_account TEXT,
    tool_type TEXT,
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
"""


def _make_db(tmp_path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db_path, f"sqlite:///{db_path}"


def _insert(conn, sender_name, message_source, date):
    conn.execute(
        "INSERT INTO daily_messages (date, tool_name, message_id, role, sender_name, message_source) "
        "VALUES (?, 'openclaw', ?, 'user', ?, ?)",
        (date, f"m-{sender_name}-{date}", sender_name, message_source),
    )


# --------------------------------------------------------------------------- #
# repo: message_source resolved from the most recent row
# --------------------------------------------------------------------------- #
class TestF3RepoMessageSourceResolution:
    def test_message_source_takes_most_recent_row(self, tmp_path):
        db_path, db_url = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        # sender "manager123": older feishu row, newer dingtalk row
        _insert(conn, "manager123", "feishu", "2026-07-01")
        _insert(conn, "manager123", "dingtalk", "2026-07-20")
        conn.commit()
        conn.close()

        repo = UserToolAccountRepository(Database(db_url))
        rows = repo.get_unmapped_tool_accounts()
        assert len(rows) == 1
        assert rows[0]["sender_name"] == "manager123"
        # Newest row wins → dingtalk, not feishu.
        assert rows[0]["message_source"] == "dingtalk"

    def test_real_dingtalk_userid_resolves_dingtalk_source(self, tmp_path):
        """A realistic DingTalk userid (no -dingtalk token) is correctly tagged
        via its structured message_source — the case the old substring heuristic
        missed entirely."""
        db_path, db_url = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert(conn, "manager12345", "dingtalk", "2026-07-10")
        conn.commit()
        conn.close()

        repo = UserToolAccountRepository(Database(db_url))
        rows = repo.get_unmapped_tool_accounts()
        assert rows[0]["message_source"] == "dingtalk"

    def test_mapped_accounts_excluded(self, tmp_path):
        db_path, db_url = _make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert(conn, "mapped_one", "dingtalk", "2026-07-10")
        _insert(conn, "free_two", "feishu", "2026-07-10")
        conn.execute(
            "INSERT INTO user_tool_accounts (user_id, tool_account, tool_type) "
            "VALUES (1, 'mapped_one', 'dingtalk')"
        )
        conn.commit()
        conn.close()

        repo = UserToolAccountRepository(Database(db_url))
        rows = repo.get_unmapped_tool_accounts()
        names = {r["sender_name"] for r in rows}
        assert names == {"free_two"}
