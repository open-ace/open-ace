"""Tests for UsageRepository.get_session_only_usage (#1269 P1, #2705, #3307).

Pins that the Work-page quota display reads session_daily_usage first,
then user_daily_stats, then agent_sessions fallback. Never reads daily_messages,
per the #1125 data-contract rule that analysis fact tables must not participate
in Workspace runtime display.

Updated for Issue #3307: Query now prioritizes session_daily_usage (per-day
incremental records) over user_daily_stats and agent_sessions.created_at fallback.
This ensures long-running WebUI sessions have their daily usage correctly
attributed to the request date, not the session creation date.
"""

from unittest.mock import MagicMock, call

import pytest

from app.repositories.usage_repo import UsageRepository


def _make_repo(db):
    repo = UsageRepository()
    repo.db = db
    return repo


class TestGetSessionOnlyUsage:
    def test_reads_session_daily_usage_first(self):
        """Fast path: session_daily_usage is queried first and its values returned
        when the table has data, without falling back to user_daily_stats or agent_sessions."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 4_063_745_521, "requests": 500}
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-08-01", end_date="2026-08-16"
        )

        sql = db.fetch_one.call_args[0][0]
        assert "FROM session_daily_usage" in sql
        assert "daily_messages" not in sql
        assert result["tokens"] == 4_063_745_521
        assert result["requests"] == 500
        # local_* legs are zeroed since daily_messages is excluded.
        assert result["local_tokens"] == 0
        assert result["local_requests"] == 0
        # Only one DB call — no fallback needed.
        assert db.fetch_one.call_count == 1

    @pytest.mark.regression
    @pytest.mark.issue(3307)
    def test_fast_path_scoped_to_user_and_date_range(self):
        """session_daily_usage query must bind user_id and the date window."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 100, "requests": 1}
        repo = _make_repo(db)

        repo.get_session_only_usage(user_id=42, start_date="2026-06-01", end_date="2026-06-25")

        params = db.fetch_one.call_args[0][1]
        assert params == (42, "2026-06-01", "2026-06-25")

    @pytest.mark.regression
    @pytest.mark.issue(3307)
    def test_falls_back_to_user_daily_stats_when_session_daily_empty(self):
        """When session_daily_usage returns zero tokens and zero requests,
        _session_usage falls back to user_daily_stats."""
        db = MagicMock()
        # First call (session_daily_usage): tokens=0, requests=0 → trigger fallback.
        # Second call (user_daily_stats): has data.
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},
            {"tokens": 9_999_999, "requests": 77},
        ]
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-08-16", end_date="2026-08-16"
        )

        assert result["tokens"] == 9_999_999
        assert result["requests"] == 77
        assert db.fetch_one.call_count == 2
        # Second call must query user_daily_stats.
        fallback_sql = db.fetch_one.call_args_list[1][0][0]
        assert "FROM user_daily_stats" in fallback_sql
        assert "daily_messages" not in fallback_sql

    @pytest.mark.regression
    @pytest.mark.issue(2705)
    def test_falls_back_to_agent_sessions_when_all_empty(self):
        """When both session_daily_usage and user_daily_stats are empty,
        _session_usage falls back to agent_sessions.total_tokens."""
        db = MagicMock()
        # First call (session_daily_usage): tokens=0, requests=0 → trigger fallback.
        # Second call (user_daily_stats): tokens=0, requests=0 → trigger fallback.
        # Third call (agent_sessions): authoritative total_tokens.
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},
            {"tokens": 0, "requests": 0},
            {"tokens": 9_999_999, "requests": 77},
        ]
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-08-16", end_date="2026-08-16"
        )

        assert result["tokens"] == 9_999_999
        assert result["requests"] == 77
        assert db.fetch_one.call_count == 3
        # Third call must query agent_sessions (daily_messages forbidden by #1125).
        fallback_sql = db.fetch_one.call_args_list[2][0][0]
        assert "FROM agent_sessions" in fallback_sql
        assert "total_tokens" in fallback_sql
        assert "daily_messages" not in fallback_sql

    def test_session_daily_table_missing_falls_back_gracefully(self):
        """If session_daily_usage raises (table doesn't exist on an older schema),
        _session_usage falls back gracefully to user_daily_stats."""
        db = MagicMock()
        db.fetch_one.side_effect = [
            Exception("no such table: session_daily_usage"),
            {"tokens": 12345, "requests": 7},
        ]
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-06-25", end_date="2026-06-25"
        )

        assert result["tokens"] == 12345
        assert result["requests"] == 7

    def test_zero_when_no_data_anywhere(self):
        """When all tables return no data, result is zeros."""
        db = MagicMock()
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},  # session_daily_usage: empty
            {"tokens": 0, "requests": 0},  # user_daily_stats: empty
            None,  # agent_sessions: no rows
        ]
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-06-25", end_date="2026-06-25"
        )

        assert result["tokens"] == 0
        assert result["requests"] == 0
        assert result["local_tokens"] == 0

    def test_never_reads_daily_messages(self):
        """None of the query paths may read daily_messages (#1125)."""
        db = MagicMock()
        # Trigger all paths: session_daily empty → user_daily empty → agent_sessions.
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},
            {"tokens": 0, "requests": 0},
            {"tokens": 1, "requests": 1},
        ]
        repo = _make_repo(db)

        repo.get_session_only_usage(user_id=1, start_date="2026-06-01", end_date="2026-06-30")

        for c in db.fetch_one.call_args_list:
            sql = c[0][0]
            assert "daily_messages" not in sql, f"Forbidden table in SQL: {sql}"