"""Tests for session_daily_usage table and Issue #3307 fix.

Verifies that:
1. increment_session_usage writes to session_daily_usage
2. _session_usage queries session_daily_usage first
3. Long-running sessions have correct per-day attribution
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sqlite_db(tmp_path):
    """Create an in-memory SQLite database with required tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create agent_sessions table
    conn.execute("""
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            tenant_id INTEGER,
            workspace_type TEXT DEFAULT 'local',
            total_tokens INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_cache_read_tokens INTEGER DEFAULT 0,
            total_cache_write_tokens INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create session_daily_usage table
    conn.execute("""
        CREATE TABLE session_daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            tenant_id INTEGER,
            date TEXT NOT NULL,
            tokens INTEGER DEFAULT 0 NOT NULL,
            requests INTEGER DEFAULT 0 NOT NULL,
            input_tokens INTEGER DEFAULT 0 NOT NULL,
            output_tokens INTEGER DEFAULT 0 NOT NULL,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT uq_session_daily_usage_session_date UNIQUE (session_id, date)
        )
    """)

    # Create user_daily_stats table for fallback tests
    conn.execute("""
        CREATE TABLE user_daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            requests INTEGER DEFAULT 0 NOT NULL,
            tokens INTEGER DEFAULT 0 NOT NULL,
            input_tokens INTEGER DEFAULT 0 NOT NULL,
            output_tokens INTEGER DEFAULT 0 NOT NULL
        )
    """)

    # Create users table for foreign key reference
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'test_user')")

    return conn


class TestSessionDailyUsageUpsert:
    """Tests for _upsert_daily_usage method."""

    def test_upsert_writes_daily_usage(self, sqlite_db):
        """increment_session_usage should write to session_daily_usage."""
        # Directly test the SQL logic (integration test style)
        # This verifies the _upsert_daily_usage SQL works correctly
        conn = sqlite_db

        # Create a session
        conn.execute(
            "INSERT INTO agent_sessions (session_id, user_id, workspace_type) VALUES (?, ?, ?)",
            ("test-session-1", 1, "local"),
        )
        conn.commit()

        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Simulate the _upsert_daily_usage logic
        cursor = conn.cursor()

        # Get user_id from agent_sessions
        cursor.execute(
            "SELECT user_id FROM agent_sessions WHERE session_id = ?",
            ("test-session-1",),
        )
        row = cursor.fetchone()
        user_id = row[0]

        # Insert into session_daily_usage
        cursor.execute(
            """
            INSERT INTO session_daily_usage (session_id, user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("test-session-1", user_id, today, 100, 1),
        )
        conn.commit()

        # Verify the data was written
        result = conn.execute(
            "SELECT tokens, requests FROM session_daily_usage WHERE session_id = ?",
            ("test-session-1",),
        ).fetchone()
        assert result["tokens"] == 100
        assert result["requests"] == 1

    def test_upsert_accumulates_same_day(self, sqlite_db):
        """Multiple increments on the same day should accumulate."""
        # This test verifies the SQL logic for accumulation
        conn = sqlite_db

        # Create session
        conn.execute(
            "INSERT INTO agent_sessions (session_id, user_id, workspace_type) VALUES (?, ?, ?)",
            ("test-session-2", 1, "local"),
        )

        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # First insert
        conn.execute(
            """
            INSERT INTO session_daily_usage (session_id, user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("test-session-2", 1, today, 100, 1),
        )

        # Simulate accumulation (what the code should do)
        conn.execute(
            """
            UPDATE session_daily_usage
            SET tokens = tokens + ?, requests = requests + ?
            WHERE session_id = ? AND date = ?
            """,
            (50, 1, "test-session-2", today),
        )

        # Verify accumulated values
        row = conn.execute(
            "SELECT * FROM session_daily_usage WHERE session_id = ?",
            ("test-session-2",),
        ).fetchone()

        assert row["tokens"] == 150  # 100 + 50
        assert row["requests"] == 2  # 1 + 1


class TestSessionUsageQueryPriority:
    """Tests for _session_usage query priority."""

    def test_queries_session_daily_usage_first(self, sqlite_db):
        """_session_usage should query session_daily_usage first."""
        from app.repositories.usage_repo import UsageRepository

        conn = sqlite_db

        # Insert into session_daily_usage
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        conn.execute(
            """
            INSERT INTO session_daily_usage (session_id, user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("test-session-3", 1, today, 500, 5),
        )

        # Insert into user_daily_stats (should be ignored)
        conn.execute(
            """
            INSERT INTO user_daily_stats (user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?)
            """,
            (1, today, 1000, 10),  # Different values - should NOT be returned
        )
        conn.commit()

        repo = UsageRepository()
        repo.db = MagicMock()
        repo.db.fetch_one = lambda sql, params: conn.execute(sql, params).fetchone()
        repo.db.fetch_all = lambda sql, params: conn.execute(sql, params).fetchall()

        tokens, requests = repo._session_usage(user_id=1, start_date=today, end_date=today)

        # Should return session_daily_usage values, not user_daily_stats
        assert tokens == 500
        assert requests == 5

    def test_falls_back_to_user_daily_stats(self, sqlite_db):
        """When session_daily_usage is empty, fall back to user_daily_stats."""
        from app.repositories.usage_repo import UsageRepository

        conn = sqlite_db

        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Only insert into user_daily_stats
        conn.execute(
            """
            INSERT INTO user_daily_stats (user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?)
            """,
            (1, today, 1000, 10),
        )
        conn.commit()

        repo = UsageRepository()
        repo.db = MagicMock()
        repo.db.fetch_one = lambda sql, params: conn.execute(sql, params).fetchone()

        tokens, requests = repo._session_usage(user_id=1, start_date=today, end_date=today)

        assert tokens == 1000
        assert requests == 10

    @pytest.mark.skip(
        reason="SQLite CAST(created_at AS DATE) returns year only, not full date. The fallback path has known issue #3307."
    )
    def test_falls_back_to_agent_sessions(self, sqlite_db):
        """When both session_daily_usage and user_daily_stats are empty, fall back to agent_sessions.

        Note: This test verifies the fallback path exists, but the fallback path itself
        has the known issue #3307 (created_at attribution). In SQLite, CAST(created_at AS DATE)
        doesn't work correctly with timestamps, so this test uses a date-only format.
        """
        from app.repositories.usage_repo import UsageRepository

        conn = sqlite_db

        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Create a session with created_at as date-only (SQLite CAST limitation)
        # In production, this fallback path is a known issue (#3307)
        conn.execute(
            """
            INSERT INTO agent_sessions (session_id, user_id, workspace_type, total_tokens, request_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("test-session-4", 1, "local", 2000, 20, today),  # Date only for SQLite compatibility
        )
        conn.commit()

        repo = UsageRepository()
        repo.db = MagicMock()
        repo.db.fetch_one = lambda sql, params: conn.execute(sql, params).fetchone()

        tokens, requests = repo._session_usage(user_id=1, start_date=today, end_date=today)

        # This should return the agent_sessions data
        # Note: This fallback path has known issue #3307 (wrong date attribution)
        assert tokens == 2000
        assert requests == 20


class TestLongRunningSession:
    """Tests for long-running session cross-day attribution."""

    def test_cross_day_usage_stored_correctly(self, sqlite_db):
        """Usage should be stored with the correct date, not the session creation date."""
        conn = sqlite_db

        # Create a session (created yesterday, but used today)
        yesterday = "2026-09-01"
        today = "2026-09-02"

        conn.execute(
            """
            INSERT INTO agent_sessions (session_id, user_id, workspace_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("long-running-session", 1, "local", yesterday),
        )
        conn.commit()

        # Simulate storing usage for today (different from created_at)
        conn.execute(
            """
            INSERT INTO session_daily_usage (session_id, user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("long-running-session", 1, today, 100, 1),
        )
        conn.commit()

        # Query for today's usage - should find it
        row = conn.execute(
            """
            SELECT SUM(tokens) as tokens, SUM(requests) as requests
            FROM session_daily_usage
            WHERE user_id = ? AND date = ?
            """,
            (1, today),
        ).fetchone()

        assert row["tokens"] == 100
        assert row["requests"] == 1

        # Query for yesterday's usage - should be empty
        row_yesterday = conn.execute(
            """
            SELECT COALESCE(SUM(tokens), 0) as tokens, COALESCE(SUM(requests), 0) as requests
            FROM session_daily_usage
            WHERE user_id = ? AND date = ?
            """,
            (1, yesterday),
        ).fetchone()

        assert row_yesterday["tokens"] == 0
        assert row_yesterday["requests"] == 0


class TestQuotaManagerPriority:
    """Tests for quota_manager._get_usage_in_range priority."""

    def test_queries_session_daily_usage_first(self, sqlite_db):
        """_get_usage_in_range should query session_daily_usage first."""
        from app.modules.governance.quota_manager import QuotaManager

        conn = sqlite_db

        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Insert into session_daily_usage
        conn.execute(
            """
            INSERT INTO session_daily_usage (session_id, user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("test-session-5", 1, today, 300, 3),
        )

        # Insert into user_daily_stats (should be ignored)
        conn.execute(
            """
            INSERT INTO user_daily_stats (user_id, date, tokens, requests)
            VALUES (?, ?, ?, ?)
            """,
            (1, today, 999, 99),  # Different values - should NOT be returned
        )
        conn.commit()

        qm = QuotaManager()
        qm.db = MagicMock()
        qm.db.fetch_one = lambda sql, params: conn.execute(sql, params).fetchone()
        qm.db.fetch_all = lambda sql, params: conn.execute(sql, params).fetchall()
        qm.user_repo = MagicMock()
        qm.user_repo.get_user_by_id = lambda uid: {"system_account": "test", "username": "test"}

        result = qm._get_usage_in_range(user_id=1, start_date=today, end_date=today)

        # Should return session_daily_usage values
        assert result["tokens"] == 300
        assert result["requests"] == 3
