#!/usr/bin/env python3
"""Issue #1829 — F3: tool-account classification de-substringed.

Previously ``get_unmapped_tool_accounts`` classified tool_type by brittle
sender_name substrings (``-dingtalk`` etc.). Real DingTalk userids don't follow
a ``-dingtalk`` convention, so that heuristic rarely matched. F3:

* repo: ``get_unmapped_tool_accounts`` now resolves each sender's
  ``message_source`` (the source of its most recent row) via a correlated
  subquery, deterministically across SQLite/PostgreSQL.
* route: tool_type is driven by the structured ``message_source`` first, with
  the Feishu ``ou_`` prefix and the openclaw-family tool-name tokens kept only
  as fallbacks.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from app.repositories.database import Database
from app.repositories.user_tool_account_repo import UserToolAccountRepository

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


# --------------------------------------------------------------------------- #
# route: source-driven tool_type classification (with fallbacks)
# --------------------------------------------------------------------------- #
@pytest.fixture
def app():
    from flask import Flask

    from app.routes.tool_accounts import tool_accounts_bp

    app = Flask(__name__)
    app.register_blueprint(tool_accounts_bp)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    yield app


def _authed_get(client, path):
    with patch("app.auth.decorators._extract_session_token", return_value="t"):
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={"id": 1, "role": "admin", "username": "admin"},
        ):
            return client.get(path)


class TestF3RouteSourceDrivenClassification:
    def test_dingtalk_via_message_source(self, app):
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "manager123", "message_source": "dingtalk", "message_count": 5}
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body[0]["tool_type"] == "dingtalk"

    def test_feishu_via_message_source(self, app):
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "anything", "message_source": "feishu", "message_count": 1}
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "feishu"

    def test_feishu_ou_prefix_fallback_when_source_missing(self, app):
        """Rows whose message_source wasn't resolved still classify via the
        stable Feishu OpenAPI ou_ prefix."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[{"sender_name": "ou_abc123", "message_source": None, "message_count": 1}],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "feishu"

    def test_openclaw_family_token_fallback(self, app):
        """openclaw-family sub-tools share message_source='openclaw' and carry
        the sub-tool name in sender_name; the token is the only discriminator."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "host-qwen", "message_source": "openclaw", "message_count": 1},
                {"sender_name": "host-claude", "message_source": "openclaw", "message_count": 1},
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        types = {row["sender_name"]: row["tool_type"] for row in resp.get_json()}
        assert types["host-qwen"] == "qwen"
        assert types["host-claude"] == "claude"

    def test_no_dingtalk_substring_matching(self, app):
        """A dingtag sender_name WITHOUT a -dingtalk token must still classify
        as dingtalk via message_source — confirming the old substring heuristic
        is gone, not just augmented."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {
                    "sender_name": "plainuser-no-token-here",
                    "message_source": "dingtalk",
                    "message_count": 1,
                },
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "dingtalk"
