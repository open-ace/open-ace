"""Tests for UsageRepository.get_session_only_usage (#1269 P1).

Pins that the Work-page quota display reads agent_sessions only and does NOT
touch daily_messages, per the #1125 data-contract rule that analysis fact
tables must not participate in Workspace runtime display.
"""

from unittest.mock import MagicMock

from app.repositories.usage_repo import UsageRepository


def _make_repo(db):
    repo = UsageRepository()
    repo.db = db
    return repo


class TestGetSessionOnlyUsage:
    def test_does_not_query_daily_messages(self):
        """The query must read agent_sessions and never daily_messages."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 12345, "requests": 7}
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-06-25", end_date="2026-06-25"
        )

        sql = db.fetch_one.call_args[0][0]
        assert "FROM agent_sessions" in sql
        assert "daily_messages" not in sql
        # Must cover all workspace types (local autonomous/terminal/remote).
        assert "workspace_type IN ('local', 'remote', 'terminal')" in sql
        assert result["tokens"] == 12345
        assert result["requests"] == 7
        # local_* legs are zeroed since daily_messages is excluded.
        assert result["local_tokens"] == 0
        assert result["local_requests"] == 0

    def test_zero_when_no_sessions(self):
        db = MagicMock()
        db.fetch_one.return_value = None
        repo = _make_repo(db)

        result = repo.get_session_only_usage(
            user_id=1, start_date="2026-06-25", end_date="2026-06-25"
        )

        assert result["tokens"] == 0
        assert result["requests"] == 0
        assert result["local_tokens"] == 0

    def test_scoped_to_user_and_date_range(self):
        """The query must bind user_id and the date window."""
        db = MagicMock()
        db.fetch_one.return_value = {"tokens": 0, "requests": 0}
        repo = _make_repo(db)

        repo.get_session_only_usage(user_id=42, start_date="2026-06-01", end_date="2026-06-25")

        params = db.fetch_one.call_args[0][1]
        assert params == (42, "2026-06-01", "2026-06-25")
