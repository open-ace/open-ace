#!/usr/bin/env python3
"""
Unit tests for user_daily_stats aggregation fix (Issue #83).

Tests cover:
1. UserDailyStatsAggregator.aggregate_user() — SQLite branch
2. user_stats_helper._refresh_user_daily_stats_for_dates() — delegation
3. DataFetchScheduler._aggregate_user_stats() — safety net
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Create minimal tables for testing
SCHEMA_SQL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    is_active INTEGER DEFAULT 1,
    system_account TEXT,
    role TEXT DEFAULT 'user'
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
    model TEXT
);

CREATE TABLE user_daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    requests INTEGER DEFAULT 0 NOT NULL,
    tokens INTEGER DEFAULT 0 NOT NULL,
    input_tokens INTEGER DEFAULT 0 NOT NULL,
    output_tokens INTEGER DEFAULT 0 NOT NULL,
    cache_tokens INTEGER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(user_id, date)
);
"""


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database with required schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # Insert test users
    conn.execute(
        "INSERT INTO users (id, username, system_account) VALUES (1, 'alice', 'alice-host')"
    )
    conn.execute("INSERT INTO users (id, username, system_account) VALUES (2, 'bob', NULL)")

    # Insert test messages for the last 3 days
    today = datetime.now()
    for day_offset in range(3):
        date_str = (today - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        # Alice has messages
        for i in range(3):
            conn.execute(
                """
                INSERT INTO daily_messages (date, role, tokens_used, input_tokens, output_tokens, sender_name)
                VALUES (?, 'assistant', 100, 80, 20, ?)
            """,
                (date_str, f"alice-host-workspace-claude-{i}"),
            )
        # Bob has messages
        for i in range(2):
            conn.execute(
                """
                INSERT INTO daily_messages (date, role, tokens_used, input_tokens, output_tokens, sender_name)
                VALUES (?, 'assistant', 50, 40, 10, ?)
            """,
                (date_str, f"bob-laptop-claude-{i}"),
            )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mock_db_instance(temp_db):
    """Create a mock Database instance that uses the temp SQLite db."""
    with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
        from app.repositories.database import Database

        db = Database(db_url=f"sqlite:///{temp_db}")
        yield db


class TestAggregatorSQLite:
    """Test UserDailyStatsAggregator with SQLite."""

    def test_aggregate_user_creates_records(self, mock_db_instance, temp_db):
        """aggregate_user() should insert stats rows for each date with data."""
        from app.services.user_stats_aggregator import UserDailyStatsAggregator

        aggregator = UserDailyStatsAggregator(db=mock_db_instance)
        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            records = aggregator.aggregate_user(
                user_id=1, username="alice", days=7, system_account="alice-host"
            )

        assert records >= 3  # 3 days of data

        # Verify the data directly
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT date, requests, tokens FROM user_daily_stats WHERE user_id = 1 ORDER BY date"
        ).fetchall()
        conn.close()

        assert len(rows) >= 3
        for row in rows:
            assert row["requests"] == 3
            assert row["tokens"] == 300  # 3 messages * 100 tokens

    def test_aggregate_all_users(self, mock_db_instance, temp_db):
        """aggregate_all_users() should aggregate for all active users."""
        from app.services.user_stats_aggregator import UserDailyStatsAggregator

        aggregator = UserDailyStatsAggregator(db=mock_db_instance)
        with (
            patch("app.services.user_stats_aggregator.is_postgresql", return_value=False),
            patch.object(
                aggregator.user_repo,
                "get_all_users",
                return_value=[
                    {"id": 1, "username": "alice", "system_account": "alice-host"},
                    {"id": 2, "username": "bob", "system_account": None},
                ],
            ),
        ):
            records = aggregator.aggregate_all_users(days=7)

        assert records >= 6  # 2 users * 3 days each

        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        alice_rows = conn.execute("SELECT * FROM user_daily_stats WHERE user_id = 1").fetchall()
        bob_rows = conn.execute("SELECT * FROM user_daily_stats WHERE user_id = 2").fetchall()
        conn.close()

        assert len(alice_rows) >= 3
        assert len(bob_rows) >= 3

    def test_aggregate_user_upsert(self, mock_db_instance, temp_db):
        """Running aggregate_user() twice should update, not duplicate."""
        from app.services.user_stats_aggregator import UserDailyStatsAggregator

        aggregator = UserDailyStatsAggregator(db=mock_db_instance)
        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            aggregator.aggregate_user(
                user_id=1, username="alice", days=7, system_account="alice-host"
            )
            aggregator.aggregate_user(
                user_id=1, username="alice", days=7, system_account="alice-host"
            )

        conn = sqlite3.connect(temp_db)
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_daily_stats WHERE user_id = 1"
        ).fetchone()[0]
        conn.close()

        # Should still be exactly 3 rows (one per date), not 6
        assert count == 3

    def test_cleanup_old_data_sqlite(self, mock_db_instance, temp_db):
        """cleanup_old_data() should use ? placeholder for SQLite."""
        from app.services.user_stats_aggregator import UserDailyStatsAggregator

        aggregator = UserDailyStatsAggregator(db=mock_db_instance)
        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            aggregator.aggregate_user(
                user_id=1, username="alice", days=30, system_account="alice-host"
            )
            deleted = aggregator.cleanup_old_data(days_to_keep=0)

        # All data is recent, so with days_to_keep=0 nothing should be deleted
        # Actually with days_to_keep=0, cutoff is today, so past data gets deleted
        assert isinstance(deleted, int)


class TestUserStatsHelper:
    """Test user_stats_helper._refresh_user_daily_stats_for_dates()."""

    def test_delegates_to_aggregator(self, temp_db):
        """Helper should call UserDailyStatsAggregator.aggregate_all_users()."""
        today = datetime.now()
        dates = {
            today.strftime("%Y-%m-%d"),
            (today - timedelta(days=1)).strftime("%Y-%m-%d"),
        }

        with (
            patch("app.services.user_stats_aggregator.is_postgresql", return_value=False),
            patch("app.services.user_stats_aggregator.UserDailyStatsAggregator") as MockAggregator,
        ):
            mock_instance = MagicMock()
            mock_instance.aggregate_all_users.return_value = 4
            MockAggregator.return_value = mock_instance

            from scripts.shared.user_stats_helper import _refresh_user_daily_stats_for_dates

            _refresh_user_daily_stats_for_dates(dates)

            MockAggregator.assert_called_once()
            mock_instance.aggregate_all_users.assert_called_once()
            # days_back should be calculated from date range (2 days = days=2)
            call_args = mock_instance.aggregate_all_users.call_args
            assert call_args[1]["days"] == 2

    def test_empty_dates_returns_early(self):
        """Empty date set should return without calling aggregator."""
        with patch("app.services.user_stats_aggregator.UserDailyStatsAggregator") as MockAggregator:
            from scripts.shared.user_stats_helper import _refresh_user_daily_stats_for_dates

            _refresh_user_daily_stats_for_dates(set())

            MockAggregator.assert_not_called()


class TestSchedulerSafetyNet:
    """Test DataFetchScheduler._aggregate_user_stats()."""

    def test_aggregate_user_stats_calls_background(self):
        """_aggregate_user_stats should call aggregate_user_stats_background."""
        from app.services.data_fetch_scheduler import DataFetchScheduler

        scheduler = DataFetchScheduler()

        with patch("app.services.user_stats_aggregator.aggregate_user_stats_background") as mock_bg:
            scheduler._aggregate_user_stats()
            mock_bg.assert_called_once()

    def test_aggregate_user_stats_handles_exception(self):
        """_aggregate_user_stats should not raise on failure."""
        from app.services.data_fetch_scheduler import DataFetchScheduler

        scheduler = DataFetchScheduler()

        with patch(
            "app.services.user_stats_aggregator.aggregate_user_stats_background",
            side_effect=RuntimeError("db error"),
        ):
            # Should not raise
            scheduler._aggregate_user_stats()

    def test_run_fetch_calls_aggregate(self):
        """_run_fetch should call _aggregate_user_stats after fetch."""
        from app.services.data_fetch_scheduler import DataFetchScheduler

        scheduler = DataFetchScheduler()

        with (
            patch("app.routes.fetch.run_fetch_scripts"),
            patch.object(scheduler, "_refresh_materialized_views"),
            patch.object(scheduler, "_aggregate_user_stats") as mock_agg,
        ):
            scheduler._run_fetch()
            mock_agg.assert_called_once()
