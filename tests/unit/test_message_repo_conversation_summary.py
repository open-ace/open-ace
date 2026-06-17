"""Unit tests for MessageRepository.get_conversation_stats_summary (method B).

The restored method is the single source of truth for per-session averages.
Tests assert: date/host filter pass-through, COUNT(DISTINCT) semantics,
scope-consistency (same id_filter for numerator & denominator), empty result,
and repo-layer caching. SQL string assertions follow the daily_stats_repo
test convention (issue #525).
"""

from unittest.mock import MagicMock

from app.repositories.message_repo import MessageRepository
from app.utils.cache import get_cache


class TestGetConversationStatsSummary:
    def setup_method(self):
        get_cache().clear()
        self.db = MagicMock()
        self.repo = MessageRepository(db=self.db)

    def test_date_and_host_filters_passed_through(self):
        self.db.fetch_one.return_value = {
            "total_conversations": 5,
            "total_messages": 50,
            "total_tokens": 500,
            "total_input_tokens": 300,
            "total_output_tokens": 200,
        }
        result = self.repo.get_conversation_stats_summary(
            start_date="2026-05-01", end_date="2026-05-23", host_name="host1"
        )

        query, params = self.db.fetch_one.call_args[0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        assert "host_name = ?" in query
        assert params == ("2026-05-01", "2026-05-23", "host1")

        # same id_filter set for numerator and denominator (scope consistency)
        assert (
            "COALESCE(conversation_id, feishu_conversation_id, agent_session_id) IS NOT NULL"
            in query
        )
        assert (
            "COUNT(DISTINCT COALESCE(conversation_id, feishu_conversation_id, agent_session_id))"
            in query
        )

        assert result["total_conversations"] == 5
        assert result["total_messages"] == 50
        assert result["total_tokens"] == 500
        # averages use the same-scope sums / distinct
        assert result["average_messages_per_conversation"] == 10.0
        assert result["average_tokens_per_conversation"] == 100.0
        assert result["avg_conversation_length"] == 10.0

    def test_no_host_omits_host_filter(self):
        self.db.fetch_one.return_value = {
            "total_conversations": 1,
            "total_messages": 1,
            "total_tokens": 1,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23")
        query, params = self.db.fetch_one.call_args[0]
        assert "host_name" not in query
        assert params == ("2026-05-01", "2026-05-23")

    def test_empty_result_returns_zeros_without_div_by_zero(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23")
        assert result["total_conversations"] == 0
        assert result["average_messages_per_conversation"] == 0
        assert result["average_tokens_per_conversation"] == 0

    def test_zero_distinct_avoids_division_by_zero(self):
        self.db.fetch_one.return_value = {
            "total_conversations": 0,
            "total_messages": 100,
            "total_tokens": 1000,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        result = self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23")
        assert result["total_conversations"] == 0
        assert result["average_messages_per_conversation"] == 0

    def test_cached_on_repeat_call(self):
        self.db.fetch_one.return_value = {
            "total_conversations": 3,
            "total_messages": 9,
            "total_tokens": 90,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23", "host1")
        self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23", "host1")
        # second call served from repo-layer cache -> DB hit once
        assert self.db.fetch_one.call_count == 1

    def test_distinct_cache_key_per_args(self):
        self.db.fetch_one.return_value = {
            "total_conversations": 1,
            "total_messages": 1,
            "total_tokens": 1,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23", "host1")
        self.repo.get_conversation_stats_summary("2026-05-01", "2026-05-23", "host2")
        # different host -> different cache key -> two DB hits
        assert self.db.fetch_one.call_count == 2
