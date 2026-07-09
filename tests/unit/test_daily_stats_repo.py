"""Unit tests for DailyStatsRepository.

Note: Repository tests use SQL string assertions to verify correct query
structure (filters, table names, column references). These assertions check
key structural elements rather than exact formatting, but may still break
on significant SQL refactoring. See issue #525 for integration test plans.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.daily_stats_repo import DailyStatsRepository
from app.utils.cache import get_cache


class TestDailyStatsRepository:
    """Tests for DailyStatsRepository."""

    def setup_method(self):
        """Clear cache before each test."""
        get_cache().clear()
        self.db = MagicMock()
        self.repo = DailyStatsRepository(db=self.db)

    # -------------------------------------------------------------------------
    # get_daily_totals
    # -------------------------------------------------------------------------

    def test_get_daily_totals_no_filters(self):
        self.db.fetch_all.return_value = [
            {
                "date": "2024-01-01",
                "total_tokens": 100,
                "total_input_tokens": 60,
                "total_output_tokens": 40,
                "message_count": 10,
            },
        ]
        result = self.repo.get_daily_totals()
        assert len(result) == 1
        assert result[0]["date"] == "2024-01-01"
        self.db.fetch_all.assert_called_once()
        call_args = self.db.fetch_all.call_args
        # No WHERE clause when no filters
        assert "WHERE" not in call_args[0][0]

    def test_get_daily_totals_with_date_range(self):
        self.db.fetch_all.return_value = []
        self.repo.get_daily_totals(start_date="2024-01-01", end_date="2024-01-31")
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        params = call_args[0][1]
        assert params == ("2024-01-01", "2024-01-31")

    def test_get_daily_totals_with_host_name(self):
        self.db.fetch_all.return_value = []
        self.repo.get_daily_totals(host_name="host1")
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "host_name = ?" in query
        params = call_args[0][1]
        assert params == ("host1",)

    def test_get_daily_totals_with_all_filters(self):
        self.db.fetch_all.return_value = []
        self.repo.get_daily_totals(
            start_date="2024-01-01", end_date="2024-01-31", host_name="host1"
        )
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        assert "host_name = ?" in query
        params = call_args[0][1]
        assert params == ("2024-01-01", "2024-01-31", "host1")

    # -------------------------------------------------------------------------
    # get_tool_totals (normalizes tool names)
    # -------------------------------------------------------------------------

    def test_get_tool_totals_no_normalization_needed(self):
        self.db.fetch_all.return_value = [
            {
                "tool_name": "claude",
                "total_tokens": 100,
                "total_input_tokens": 60,
                "total_output_tokens": 40,
                "message_count": 10,
            },
            {
                "tool_name": "qwen",
                "total_tokens": 200,
                "total_input_tokens": 120,
                "total_output_tokens": 80,
                "message_count": 20,
            },
        ]
        result = self.repo.get_tool_totals()
        assert len(result) == 2
        # Sorted by total_tokens DESC
        assert result[0]["tool_name"] == "qwen"
        assert result[1]["tool_name"] == "claude"

    def test_get_tool_totals_merges_aliases(self):
        """Tool names like 'qwen-code' and 'qwen-code-cli' should merge into 'qwen'."""
        self.db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "total_tokens": 100,
                "total_input_tokens": 60,
                "total_output_tokens": 40,
                "message_count": 5,
            },
            {
                "tool_name": "qwen-code-cli",
                "total_tokens": 50,
                "total_input_tokens": 30,
                "total_output_tokens": 20,
                "message_count": 3,
            },
            {
                "tool_name": "claude-code",
                "total_tokens": 200,
                "total_input_tokens": 150,
                "total_output_tokens": 50,
                "message_count": 15,
            },
        ]
        result = self.repo.get_tool_totals()
        # Should merge qwen-code + qwen-code-cli -> qwen
        qwen_row = next(r for r in result if r["tool_name"] == "qwen")
        assert qwen_row["total_tokens"] == 150
        assert qwen_row["total_input_tokens"] == 90
        assert qwen_row["total_output_tokens"] == 60
        assert qwen_row["message_count"] == 8
        # Should merge claude-code -> claude
        claude_row = next(r for r in result if r["tool_name"] == "claude")
        assert claude_row["total_tokens"] == 200

    def test_get_tool_totals_handles_none_values(self):
        """None values from DB are preserved on first entry (no merge)."""
        self.db.fetch_all.return_value = [
            {
                "tool_name": "unknown",
                "total_tokens": None,
                "total_input_tokens": None,
                "total_output_tokens": None,
                "message_count": None,
            },
        ]
        result = self.repo.get_tool_totals()
        assert len(result) == 1
        # First entry without merging preserves raw DB values (None)
        assert result[0]["total_tokens"] is None
        assert result[0]["total_input_tokens"] is None
        assert result[0]["total_output_tokens"] is None
        assert result[0]["message_count"] is None

    def test_get_tool_totals_with_filters(self):
        self.db.fetch_all.return_value = []
        self.repo.get_tool_totals(start_date="2024-01-01", end_date="2024-12-31")
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query

    # -------------------------------------------------------------------------
    # get_user_totals
    # -------------------------------------------------------------------------

    def test_get_user_totals_no_filters(self):
        self.db.fetch_all.return_value = [
            {
                "sender_name": "alice",
                "total_tokens": 500,
                "total_input_tokens": 300,
                "total_output_tokens": 200,
                "message_count": 25,
            },
        ]
        result = self.repo.get_user_totals()
        assert len(result) == 1
        assert result[0]["sender_name"] == "alice"
        # Should always have sender_name IS NOT NULL condition
        query = self.db.fetch_all.call_args[0][0]
        assert "sender_name IS NOT NULL" in query

    def test_get_user_totals_with_filters(self):
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals(start_date="2024-01-01", end_date="2024-01-31", host_name="host1")
        query = self.db.fetch_all.call_args[0][0]
        assert "sender_name IS NOT NULL" in query
        assert "date >= ?" in query
        assert "date <= ?" in query
        assert "host_name = ?" in query

    # -------------------------------------------------------------------------
    # get_hourly_totals (cached!)
    # -------------------------------------------------------------------------

    def test_get_hourly_totals_basic(self):
        self.db.fetch_all.return_value = [
            {"hour": 8, "tokens": 100, "requests": 10},
            {"hour": 9, "tokens": 200, "requests": 20},
        ]
        result = self.repo.get_hourly_totals()
        assert len(result) == 2
        assert result[0]["hour"] == 8
        assert result[0]["tokens"] == 100
        assert result[0]["requests"] == 10

    def test_get_hourly_totals_converts_hour_to_int(self):
        """Hour should be converted from string to int."""
        self.db.fetch_all.return_value = [
            {"hour": "14", "tokens": 50, "requests": 5},
        ]
        result = self.repo.get_hourly_totals()
        assert result[0]["hour"] == 14
        assert isinstance(result[0]["hour"], int)

    def test_get_hourly_totals_handles_none_tokens(self):
        self.db.fetch_all.return_value = [
            {"hour": 0, "tokens": None, "requests": None},
        ]
        result = self.repo.get_hourly_totals()
        assert result[0]["tokens"] == 0
        assert result[0]["requests"] == 0

    def test_get_hourly_totals_cached(self):
        """Second call should return cached result without hitting DB again."""
        self.db.fetch_all.return_value = [
            {"hour": 10, "tokens": 100, "requests": 10},
        ]
        result1 = self.repo.get_hourly_totals()
        result2 = self.repo.get_hourly_totals()
        assert result1 == result2
        # fetch_all should only be called once due to caching
        assert self.db.fetch_all.call_count == 1

    def test_get_hourly_totals_with_filters(self):
        self.db.fetch_all.return_value = []
        self.repo.get_hourly_totals(start_date="2024-01-01", end_date="2024-12-31")
        query = self.db.fetch_all.call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query

    def test_get_hourly_totals_empty_result(self):
        self.db.fetch_all.return_value = []
        result = self.repo.get_hourly_totals()
        assert result == []

    # -------------------------------------------------------------------------
    # get_conversation_stats
    # -------------------------------------------------------------------------

    def test_get_conversation_stats_no_host(self):
        self.db.fetch_one.side_effect = [
            {"total_messages": 100, "total_tokens": 5000},
            {"unique_dates": 10},
            {"unique_tools": 5},
        ]
        with pytest.warns(DeprecationWarning):
            result = self.repo.get_conversation_stats()
        assert result["total_messages"] == 100
        assert result["total_tokens"] == 5000
        assert result["total_conversations"] == 50  # 10 * 5
        assert result["average_messages_per_conversation"] == 2.0  # 100 / 50
        assert result["average_tokens_per_conversation"] == 100.0  # 5000 / 50

    def test_get_conversation_stats_with_host(self):
        self.db.fetch_one.side_effect = [
            {"total_messages": 50, "total_tokens": 2000},
            {"unique_dates": 5},
            {"unique_tools": 2},
        ]
        with pytest.warns(DeprecationWarning):
            result = self.repo.get_conversation_stats(host_name="host1")
        assert result["total_conversations"] == 10  # 5 * 2
        assert result["total_messages"] == 50
        # Verify host_name filter is applied
        for call in self.db.fetch_one.call_args_list:
            query = call[0][0]
            assert "host_name = ?" in query

    def test_get_conversation_stats_no_result(self):
        self.db.fetch_one.return_value = None
        with pytest.warns(DeprecationWarning):
            result = self.repo.get_conversation_stats()
        assert result["total_conversations"] == 0
        assert result["total_messages"] == 0
        assert result["total_tokens"] == 0
        assert result["average_messages_per_conversation"] == 0
        assert result["average_tokens_per_conversation"] == 0

    def test_get_conversation_stats_zero_conversations(self):
        """Edge case: estimated conversations = 0 should avoid division by zero."""
        self.db.fetch_one.side_effect = [
            {"total_messages": 100, "total_tokens": 5000},
            {"unique_dates": 0},
            {"unique_tools": 0},
        ]
        with pytest.warns(DeprecationWarning):
            result = self.repo.get_conversation_stats()
        assert result["total_conversations"] == 0
        assert result["average_messages_per_conversation"] == 0

    # -------------------------------------------------------------------------
    # get_batch_aggregates
    # -------------------------------------------------------------------------

    def test_get_batch_aggregates_basic(self):
        self.db.fetch_one.return_value = {
            "total_messages": 1000,
            "total_tokens": 50000,
            "total_input_tokens": 30000,
            "total_output_tokens": 20000,
            "unique_tools": 3,
            "unique_hosts": 2,
            "unique_users": 10,
            "unique_days": 30,
        }
        result = self.repo.get_batch_aggregates()
        assert result["total_messages"] == 1000
        assert result["total_tokens"] == 50000
        assert result["unique_tools"] == 3
        assert result["unique_hosts"] == 2
        assert result["unique_users"] == 10
        assert result["unique_days"] == 30

    def test_get_batch_aggregates_no_result(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_batch_aggregates()
        assert result["total_messages"] == 0
        assert result["total_tokens"] == 0
        assert result["unique_tools"] == 0

    def test_get_batch_aggregates_with_none_values(self):
        self.db.fetch_one.return_value = {
            "total_messages": None,
            "total_tokens": None,
            "total_input_tokens": None,
            "total_output_tokens": None,
            "unique_tools": None,
            "unique_hosts": None,
            "unique_users": None,
            "unique_days": None,
        }
        result = self.repo.get_batch_aggregates()
        assert result["total_messages"] == 0
        assert result["total_tokens"] == 0

    def test_get_batch_aggregates_with_filters(self):
        self.db.fetch_one.return_value = {
            "total_messages": 100,
            "total_tokens": 5000,
            "total_input_tokens": 3000,
            "total_output_tokens": 2000,
            "unique_tools": 1,
            "unique_hosts": 1,
            "unique_users": 5,
            "unique_days": 10,
        }
        self.repo.get_batch_aggregates(start_date="2024-01-01", host_name="h1")
        query = self.db.fetch_one.call_args[0][0]
        assert "date >= ?" in query
        assert "host_name = ?" in query

    # -------------------------------------------------------------------------
    # refresh_stats
    # -------------------------------------------------------------------------

    @patch("app.repositories.daily_stats_repo.is_postgresql", return_value=False)
    def test_refresh_stats_specific_date_sqlite(self, mock_pg):
        self.db.execute.return_value = MagicMock()
        result = self.repo.refresh_stats(date="2024-01-15")
        assert result is True
        # SQLite uses INSERT OR REPLACE (single statement, no separate DELETE)
        assert self.db.execute.call_count == 1
        insert_call = self.db.execute.call_args
        assert "INSERT OR REPLACE INTO daily_stats" in insert_call[0][0]
        assert "date = ?" in insert_call[0][0]

    @patch("app.repositories.daily_stats_repo.is_postgresql", return_value=False)
    def test_refresh_stats_all_dates_sqlite(self, mock_pg):
        self.db.execute.return_value = MagicMock()
        result = self.repo.refresh_stats()
        assert result is True
        insert_call = self.db.execute.call_args
        assert "1=1" in insert_call[0][0]

    @patch("app.repositories.daily_stats_repo.is_postgresql", return_value=True)
    def test_refresh_stats_postgresql(self, mock_pg):
        self.db.execute.return_value = MagicMock()
        result = self.repo.refresh_stats(date="2024-01-15")
        assert result is True
        # PostgreSQL: DELETE + INSERT (2 execute calls)
        assert self.db.execute.call_count == 2
        insert_call = self.db.execute.call_args_list[1]
        assert "INSERT INTO daily_stats" in insert_call[0][0]
        assert "OR REPLACE" not in insert_call[0][0]

    def test_refresh_stats_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.refresh_stats()
        assert result is False

    # -------------------------------------------------------------------------
    # needs_refresh
    # -------------------------------------------------------------------------

    def test_needs_refresh_empty_stats(self):
        self.db.fetch_one.return_value = {"count": 0}
        result = self.repo.needs_refresh()
        assert result is True

    def test_needs_refresh_no_result(self):
        self.db.fetch_one.return_value = None
        result = self.repo.needs_refresh()
        assert result is True

    def test_needs_refresh_stats_stale(self):
        """Messages have newer data than stats."""
        self.db.fetch_one.side_effect = [
            {"count": 100},  # daily_stats not empty
            {"max_date": "2024-01-10"},  # stats max date
            {"max_date": "2024-01-15"},  # messages max date (newer)
        ]
        result = self.repo.needs_refresh()
        assert result is True

    def test_needs_refresh_stats_up_to_date(self):
        """Stats are up to date."""
        self.db.fetch_one.side_effect = [
            {"count": 100},  # daily_stats not empty
            {"max_date": "2024-01-15"},  # stats max date
            {"max_date": "2024-01-15"},  # messages max date (same)
            {"count": 0},  # no NULL sender_name
        ]
        result = self.repo.needs_refresh()
        assert result is False

    def test_needs_refresh_no_message_data(self):
        """No messages at all - no refresh needed."""
        self.db.fetch_one.side_effect = [
            {"count": 100},  # daily_stats not empty
            {"max_date": "2024-01-15"},  # stats max date
            None,  # no messages
            {"count": 0},  # no NULL sender_name (no messages to sync)
        ]
        result = self.repo.needs_refresh()
        assert result is False

    def test_needs_refresh_null_sender_name(self):
        """Stats have NULL sender_name that should have data."""
        self.db.fetch_one.side_effect = [
            {"count": 100},  # daily_stats not empty
            {"max_date": "2024-01-15"},  # stats max date
            {"max_date": "2024-01-15"},  # messages max date (same)
            {"count": 5},  # 5 rows with NULL sender_name that should have data
        ]
        result = self.repo.needs_refresh()
        assert result is True

    # -------------------------------------------------------------------------
    # refresh_hourly_stats
    # -------------------------------------------------------------------------

    @patch("app.repositories.daily_stats_repo.is_postgresql", return_value=False)
    def test_refresh_hourly_stats_specific_date_sqlite(self, mock_pg):
        self.db.execute.return_value = MagicMock()
        result = self.repo.refresh_hourly_stats(date="2024-01-15")
        assert result is True
        # SQLite uses INSERT OR REPLACE (single statement)
        assert self.db.execute.call_count == 1
        insert_call = self.db.execute.call_args
        assert "INSERT OR REPLACE INTO hourly_stats" in insert_call[0][0]
        assert "strftime" in insert_call[0][0]

    @patch("app.repositories.daily_stats_repo.is_postgresql", return_value=True)
    def test_refresh_hourly_stats_postgresql(self, mock_pg):
        self.db.execute.return_value = MagicMock()
        result = self.repo.refresh_hourly_stats()
        assert result is True
        # PostgreSQL: DELETE + INSERT (2 calls)
        assert self.db.execute.call_count == 2
        insert_call = self.db.execute.call_args_list[1]
        assert "INSERT INTO hourly_stats" in insert_call[0][0]
        assert "EXTRACT" in insert_call[0][0]

    def test_refresh_hourly_stats_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.refresh_hourly_stats()
        assert result is False

    # -------------------------------------------------------------------------
    # get_data_range
    # -------------------------------------------------------------------------

    def test_get_data_range_basic(self):
        """Test get_data_range returns min and max dates."""
        self.db.fetch_one.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-12-31",
        }
        result = self.repo.get_data_range()
        assert result is not None
        assert result["min_date"] == "2024-01-01"
        assert result["max_date"] == "2024-12-31"

    def test_get_data_range_empty_table(self):
        """Test get_data_range returns None when table is empty."""
        self.db.fetch_one.return_value = {"min_date": None, "max_date": None}
        result = self.repo.get_data_range()
        assert result is None

    def test_get_data_range_no_result(self):
        """Test get_data_range returns None when query returns no result."""
        self.db.fetch_one.return_value = None
        result = self.repo.get_data_range()
        assert result is None

    def test_get_data_range_query_structure(self):
        """Test that get_data_range uses correct SQL structure."""
        self.db.fetch_one.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-12-31",
        }
        self.repo.get_data_range()
        query = self.db.fetch_one.call_args[0][0]
        # Should query MIN and MAX of date column
        assert "MIN(date)" in query
        assert "MAX(date)" in query
        assert "FROM daily_stats" in query
        # Should NOT have WHERE clause (global data range)
        assert "WHERE" not in query

    def test_get_data_range_no_host_filter(self):
        """Test that get_data_range always queries global data (no host filter)."""
        self.db.fetch_one.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-12-31",
        }
        result = self.repo.get_data_range()
        assert result is not None
        # Query should NOT contain any host_name filter or WHERE clause
        query = self.db.fetch_one.call_args[0][0]
        assert "host_name" not in query
        assert "WHERE" not in query

    # -------------------------------------------------------------------------
    # get_user_totals - Feishu ID filtering
    # -------------------------------------------------------------------------

    def test_get_user_totals_filters_feishu_ids(self):
        """Verify get_user_totals() SQL includes ou_ filter condition."""
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals()
        query = self.db.fetch_all.call_args[0][0]
        # Should contain the ou_ filter condition (%% is escaped %)
        assert "NOT (ds.sender_name LIKE 'ou_%%' AND LENGTH(ds.sender_name) > 10)" in query

    def test_get_user_totals_filters_ou_prefix_variants(self):
        """Long ou_ names (length > 10) should be filtered in SQL."""
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals()
        query = self.db.fetch_all.call_args[0][0]
        # Verify both parts of the condition are present (%% is escaped %)
        assert "LIKE 'ou_%%'" in query
        assert "LENGTH(ds.sender_name) > 10" in query

    def test_get_user_totals_keeps_valid_senders(self):
        """Short ou_ names (length <= 10) should NOT be filtered."""
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals()
        query = self.db.fetch_all.call_args[0][0]
        # The filter uses NOT (...) so short ou_ names pass through
        assert "NOT (" in query

    def test_get_user_totals_filters_placeholder_values(self):
        """Verify get_user_totals() SQL filters placeholder values."""
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals()
        query = self.db.fetch_all.call_args[0][0]
        # Should filter placeholder values
        assert "NOT IN" in query
        assert "'null'" in query
        assert "'None'" in query
        assert "'undefined'" in query
        assert "'N/A'" in query
        assert "'Unknown'" in query
        assert "'unknown'" in query

    def test_get_user_totals_filters_placeholder_format(self):
        """Verify get_user_totals() SQL filters <...> placeholder format."""
        self.db.fetch_all.return_value = []
        self.repo.get_user_totals()
        query = self.db.fetch_all.call_args[0][0]
        # Should filter placeholder format <...> (%% is escaped %)
        assert "NOT LIKE '<%%>'" in query

    # -------------------------------------------------------------------------
    # get_batch_aggregates - unique_users excludes Feishu IDs
    # -------------------------------------------------------------------------

    def test_get_batch_aggregates_unique_users_excludes_feishu_ids(self):
        """Verify unique_users calculation excludes ou_ prefix users."""
        self.db.fetch_one.return_value = {
            "total_messages": 1000,
            "total_tokens": 50000,
            "total_input_tokens": 30000,
            "total_output_tokens": 20000,
            "unique_tools": 3,
            "unique_hosts": 2,
            "unique_users": 8,
            "unique_days": 30,
        }
        self.repo.get_batch_aggregates()
        query = self.db.fetch_one.call_args[0][0]
        # unique_users should use CASE WHEN with Feishu filter
        assert "CASE WHEN" in query
        # %% is escaped % for psycopg2/SQLite compatibility
        assert "NOT (sender_name LIKE 'ou_%%' AND LENGTH(sender_name) > 10)" in query
        # total_messages/tokens should NOT have the CASE filter
        assert "SUM(message_count)" in query
        assert "SUM(total_tokens)" in query
