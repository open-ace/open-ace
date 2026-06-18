"""Unit tests for MessageRepository.get_conversation_stats_summary.

These tests mock the database layer (following the pattern in
test_daily_stats_repo.py) and assert the real, date-scoped conversation
statistics computation: distinct conversation count, total messages (grain =
1 row per message), multi-turn ratio and backward-compatible aliases.

The implementation rolls every metric out of a single per-session aggregate
(one DB round-trip), so each non-empty case mocks exactly one ``fetch_one``
return row carrying ``total_conversations`` / ``total_messages`` /
``total_tokens`` / ``multi_turn_session_count``.
"""

from unittest.mock import MagicMock

from app.repositories.message_repo import MessageRepository


class TestGetConversationStatsSummary:
    """Tests for the single-source-of-truth session stats query."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = MessageRepository(db=self.db)

    def _row(self, **overrides):
        row = {
            "total_conversations": 2,
            "total_messages": 5,
            "total_tokens": 100,
            "multi_turn_session_count": 1,
        }
        row.update(overrides)
        return row

    def test_real_counts_and_multi_turn_ratio(self):
        """Distinct conversations, message grain, and >=2-message ratio from one query."""
        self.db.fetch_one.return_value = self._row(
            total_conversations=2, total_messages=5, total_tokens=100, multi_turn_session_count=1
        )

        result = self.repo.get_conversation_stats_summary(
            start_date="2026-06-01", end_date="2026-06-18", host_name="host-1"
        )

        assert result["total_conversations"] == 2
        assert result["total_messages"] == 5
        assert result["multi_turn_session_count"] == 1
        assert result["multi_turn_ratio"] == 0.5
        # averages derived from real grain
        assert result["average_messages_per_conversation"] == 2.5
        assert result["average_tokens_per_conversation"] == 50.0
        # backward-compatible alias preserved for health score / insights / exports
        assert result["avg_conversation_length"] == 2.5

        # A single per-session aggregate carries the date scope and session filter.
        assert self.db.fetch_one.call_count == 1
        query = self.db.fetch_one.call_args_list[0].args[0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        assert "host_name = ?" in query
        # session id filter is always applied
        assert (
            "COALESCE(conversation_id, feishu_conversation_id, agent_session_id) IS NOT NULL"
            in query
        )
        # multi-turn is computed inline (CASE WHEN cnt >= 2), not via a HAVING sub-query.
        assert "cnt >= 2" in query
        assert "HAVING" not in query

    def test_all_single_message_conversations_yield_zero_ratio(self):
        """No conversation has >= 2 messages -> ratio 0%."""
        self.db.fetch_one.return_value = self._row(
            total_conversations=3, total_messages=3, multi_turn_session_count=0
        )
        result = self.repo.get_conversation_stats_summary()
        assert result["multi_turn_session_count"] == 0
        assert result["multi_turn_ratio"] == 0

    def test_all_multi_turn_conversations_yield_full_ratio(self):
        """Every conversation has >= 2 messages -> ratio 100%."""
        self.db.fetch_one.return_value = self._row(
            total_conversations=4, total_messages=10, multi_turn_session_count=4
        )
        result = self.repo.get_conversation_stats_summary()
        assert result["multi_turn_session_count"] == 4
        assert result["multi_turn_ratio"] == 1.0

    def test_empty_dataset_returns_zeroed_shape(self):
        """No rows / DB returns None -> safe zero dict with every key, no second query."""
        self.db.fetch_one.return_value = None
        result = self.repo.get_conversation_stats_summary(
            start_date="2026-06-01", end_date="2026-06-18"
        )
        assert result["total_conversations"] == 0
        assert result["total_messages"] == 0
        assert result["multi_turn_ratio"] == 0
        assert result["avg_conversation_length"] == 0
        assert self.db.fetch_one.call_count == 1

    def test_zero_count_row_returns_zeroed_shape(self):
        """A real empty set yields a zero-count row (COUNT(*) = 0); treat as empty."""
        self.db.fetch_one.return_value = self._row(
            total_conversations=0, total_messages=0, total_tokens=0, multi_turn_session_count=0
        )
        result = self.repo.get_conversation_stats_summary()
        assert result["total_conversations"] == 0
        assert result["multi_turn_ratio"] == 0
        assert result["average_tokens_per_conversation"] == 0

    def test_no_date_range_still_filters_session_ids(self):
        """Without date range the session-id filter still applies (no KeyError)."""
        self.db.fetch_one.return_value = self._row()
        self.repo.get_conversation_stats_summary()
        query = self.db.fetch_one.call_args_list[0].args[0]
        assert "date >= ?" not in query
        assert "date <= ?" not in query
        assert "IS NOT NULL" in query

    def test_multi_turn_ratio_equals_count_over_total(self):
        """multi_turn_ratio must equal multi_turn_session_count / total_conversations."""
        self.db.fetch_one.return_value = self._row(
            total_conversations=5, total_messages=8, multi_turn_session_count=3
        )
        result = self.repo.get_conversation_stats_summary()
        assert result["multi_turn_session_count"] == 3
        assert result["total_conversations"] == 5
        assert result["multi_turn_ratio"] == 3 / 5
