"""Test for Issue #811: Filter out 'unknown' user in request stats by user.

This test verifies that get_request_stats_by_user properly filters out
unidentifiable users with username 'unknown'.
"""

import sqlite3
from datetime import datetime

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(811)]

# Minimal schema (same shape as tests/unit/test_usage_repo_aggregation_consistency.py,
# which these tests were originally written against — the shared tmp_db
# fixture vanished from the conftest during its test-config rework).
SCHEMA_SQL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL DEFAULT '',
    is_active INTEGER DEFAULT 1,
    system_account TEXT,
    role TEXT DEFAULT 'user',
    tenant_id INTEGER
);

CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    quota TEXT DEFAULT '{}'
);

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
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database with required schema."""
    from app.repositories.database import Database

    # get_request_stats_by_user builds its query shape from the GLOBAL
    # backend flag, not from this injected instance (see Database._adapt_sql's
    # docstring for the same class of mismatch) — pin it to the backend the
    # connection actually opens so a PostgreSQL-configured dev box exercises
    # the same SQLite branch the lane does.
    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    db = Database(db_url=f"sqlite:///{db_path}")
    yield db


class TestUnknownUserFilter:
    """Tests for filtering 'unknown' user in request statistics."""

    @pytest.fixture
    def setup_test_data(self, tmp_db):
        """Set up test data with known and unknown users."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Insert test user (password_hash is required)
        tmp_db.execute(
            """
            INSERT INTO users (username, email, password_hash, system_account)
            VALUES (?, ?, ?, ?)
            """,
            ("testuser", "test@example.com", "dummy_hash", "testuser-host-tool"),
        )

        # Insert messages with known user (matched via sender_name pattern)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 1000, "testuser-host-tool", "claude", "host"),
        )

        # Insert messages with unknown user (sender_name is empty/null)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 500, None, "claude", "host"),
        )

        # Insert messages with another identifiable user
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, role, tokens_used, sender_name, tool_name, host_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, "assistant", 2000, "otheruser-host-qwen", "qwen", "host"),
        )

        return today

    def test_unknown_user_filtered_out(self, tmp_db, setup_test_data):
        """Verify that 'unknown' users are filtered out from results."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Should only return identifiable users, not 'unknown'
        usernames = [stat["user"] for stat in stats]

        # 'unknown' should NOT be in the results
        assert "unknown" not in usernames

        # Should have identifiable users
        assert len(stats) > 0
        assert all(stat["user"] != "unknown" for stat in stats)

    def test_known_users_still_returned(self, tmp_db, setup_test_data):
        """Verify that known users are still returned correctly."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Should have testuser and otheruser
        usernames = [stat["user"] for stat in stats]

        # Known users should be present
        assert "testuser" in usernames and "otheruser" in usernames

    def test_request_counts_correct(self, tmp_db, setup_test_data):
        """Verify request counts for known users are correct."""
        from app.repositories.usage_repo import UsageRepository

        repo = UsageRepository(db=tmp_db)
        stats = repo.get_request_stats_by_user()

        # Each identifiable user should have 1 request (per tool)
        for stat in stats:
            assert stat["requests"] >= 1
            assert stat["user"] != "unknown"
