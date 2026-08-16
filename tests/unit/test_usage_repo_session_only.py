"""Tests for UsageRepository.get_session_only_usage (#1269 P1, #2705).

Pins that the Work-page quota display reads user_daily_stats (same source as
quota enforcement) and never daily_messages, per the #1125 data-contract rule
that analysis fact tables must not participate in Workspace runtime display.

Updated for Issue #2705: Query now reads user_daily_stats first (matching
quota enforcement) and falls back to agent_sessions.total_tokens when
user_daily_stats has no data for the period. This closes the ~10% gap where
the previous session_messages SUM missed cache and other non-message token costs.
"""

from unittest.mock import MagicMock, call

import pytest

from app.repositories.usage_repo import UsageRepository


def _make_repo(db):
    repo = UsageRepository()
    repo.db = db
    return repo


class TestGetSessionOnlyUsage:
    def test_reads_user_daily_stats_first(self):
        """Fast path: user_daily_stats is queried first and its values returned
        when the table has data, without falling back to agent_sessions."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 4_063_745_521, "requests": 500}
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-08-01", end_date="2026-08-16"
        )

        sql = db.fetch_one.call_args[0][0]
        assert "FROM user_daily_stats" in sql
        assert "daily_messages" not in sql
        assert result["tokens"] == 4_063_745_521
        assert result["requests"] == 500
        # local_* legs are zeroed since daily_messages is excluded.
        assert result["local_tokens"] == 0
        assert result["local_requests"] == 0
        # Only one DB call — no fallback needed.
        assert db.fetch_one.call_count == 1

    @pytest.mark.regression
    @pytest.mark.issue(2705)
    def test_fast_path_scoped_to_user_and_date_range(self):
        """user_daily_stats query must bind user_id and the date window."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 100, "requests": 1}
        repo = _make_repo(db)

        repo.get_session_only_usage(user_id=42, start_date="2026-06-01", end_date="2026-06-25")

        params = db.fetch_one.call_args[0][1]
        assert params == (42, "2026-06-01", "2026-06-25")

    @pytest.mark.regression
    @pytest.mark.issue(2705)
    def test_falls_back_to_agent_sessions_when_daily_stats_empty(self):
        """When user_daily_stats returns zero tokens and zero requests (e.g. the
        aggregator hasn't run yet for today), _session_usage falls back to
        agent_sessions.total_tokens — the enforcement legacy path."""
        db = MagicMock()
        # First call (user_daily_stats): tokens=0, requests=0 → trigger fallback.
        # Second call (agent_sessions): authoritative total_tokens.
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
        # Second call must query agent_sessions (not session_messages, not daily_messages).
        fallback_sql = db.fetch_one.call_args_list[1][0][0]
        assert "FROM agent_sessions" in fallback_sql
        assert "total_tokens" in fallback_sql
        assert "session_messages" not in fallback_sql
        assert "daily_messages" not in fallback_sql

    @pytest.mark.regression
    @pytest.mark.issue(2705)
    def test_daily_stats_table_missing_falls_back_to_agent_sessions(self):
        """If user_daily_stats raises (table doesn't exist on an older schema),
        _session_usage falls back gracefully to agent_sessions."""
        db = MagicMock()
        db.fetch_one.side_effect = [
            Exception("no such table: user_daily_stats"),
            {"tokens": 12345, "requests": 7},
        ]
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-06-25", end_date="2026-06-25"
        )

        assert result["tokens"] == 12345
        assert result["requests"] == 7

    def test_zero_when_no_data_anywhere(self):
        """When both user_daily_stats and agent_sessions return no data, result is zeros."""
        db = MagicMock()
        db.fetch_one.side_effect = [
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
        """Neither the fast path nor the fallback may read daily_messages (#1125)."""
        db = MagicMock()
        # Trigger both paths: daily_stats empty → fallback fires.
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},
            {"tokens": 1, "requests": 1},
        ]
        repo = _make_repo(db)

        repo.get_session_only_usage(user_id=1, start_date="2026-06-01", end_date="2026-06-30")

        for c in db.fetch_one.call_args_list:
            sql = c[0][0]
            assert "daily_messages" not in sql, f"Forbidden table in SQL: {sql}"
