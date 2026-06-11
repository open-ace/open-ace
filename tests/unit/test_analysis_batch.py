"""Unit tests for Analysis Service batch analysis response.

Tests that batch analysis API response includes data_range field.
"""

from concurrent.futures import Future
from unittest.mock import MagicMock, call, patch

import pytest

from app.repositories.daily_stats_repo import DailyStatsRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.usage_repo import UsageRepository
from app.services.analysis_service import AnalysisService
from app.utils.cache import get_cache


class TestBatchAnalysisDataRange:
    """Tests for data_range in batch analysis response."""

    def setup_method(self):
        """Clear cache before each test."""
        get_cache().clear()
        self.daily_stats_repo = MagicMock(spec=DailyStatsRepository)
        self.message_repo = MagicMock(spec=MessageRepository)
        self.usage_repo = MagicMock(spec=UsageRepository)
        self.service = AnalysisService(
            usage_repo=self.usage_repo,
            message_repo=self.message_repo,
            daily_stats_repo=self.daily_stats_repo,
        )
        # Default mock for count_conversations (new parallel query)
        self.message_repo.count_conversations.return_value = 50

    def test_batch_analysis_includes_data_range(self):
        """Test that batch analysis response includes data_range field."""
        # Mock concurrent query results
        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 1000,
            "total_tokens": 50000,
            "total_input_tokens": 30000,
            "total_output_tokens": 20000,
            "unique_tools": 3,
            "unique_hosts": 2,
            "unique_users": 10,
            "unique_days": 30,
        }
        self.daily_stats_repo.get_user_totals.return_value = []
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {
            "total_conversations": 50,
            "total_messages": 100,
            "total_tokens": 5000,
        }
        self.daily_stats_repo.get_data_range.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-12-31",
        }
        self.usage_repo.get_request_count_total.return_value = 100

        result = self.service.get_batch_analysis()

        # Verify data_range is included in response
        assert "data_range" in result
        assert result["data_range"] is not None
        assert result["data_range"]["min_date"] == "2024-01-01"
        assert result["data_range"]["max_date"] == "2024-12-31"

        # Verify get_data_range was called
        self.daily_stats_repo.get_data_range.assert_called_once()

    def test_batch_analysis_data_range_none_when_empty(self):
        """Test that data_range is None when database is empty."""
        # Mock empty database results
        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 0,
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "unique_tools": 0,
            "unique_hosts": 0,
            "unique_users": 0,
            "unique_days": 0,
        }
        self.daily_stats_repo.get_user_totals.return_value = []
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {
            "total_conversations": 0,
            "total_messages": 0,
            "total_tokens": 0,
        }
        # get_data_range returns None when table is empty
        self.daily_stats_repo.get_data_range.return_value = None
        self.usage_repo.get_request_count_total.return_value = 0

        result = self.service.get_batch_analysis()

        # Verify data_range is None when table is empty
        assert "data_range" in result
        assert result["data_range"] is None

    def test_batch_analysis_calls_data_range_no_args(self):
        """Test that get_data_range is called without arguments (global range)."""
        # Mock minimal results
        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 10,
            "total_tokens": 100,
            "unique_tools": 1,
            "unique_hosts": 1,
            "unique_users": 1,
            "unique_days": 1,
        }
        self.daily_stats_repo.get_user_totals.return_value = []
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {}
        self.daily_stats_repo.get_data_range.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-01-01",
        }
        self.usage_repo.get_request_count_total.return_value = 10

        # Call with host filter
        result = self.service.get_batch_analysis(host_name="host1")

        # Verify get_data_range was called without arguments (global range)
        self.daily_stats_repo.get_data_range.assert_called_once_with()

        # Verify data_range is in response
        assert result["data_range"] is not None

    def test_batch_analysis_user_ranking_no_feishu_ids(self):
        """Test that user_ranking excludes ou_ prefix users (Feishu Open IDs)."""
        # Mock user totals including ou_ users
        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 1000,
            "total_tokens": 50000,
            "total_input_tokens": 30000,
            "total_output_tokens": 20000,
            "unique_tools": 3,
            "unique_hosts": 2,
            "unique_users": 2,
            "unique_days": 30,
        }
        self.daily_stats_repo.get_user_totals.return_value = [
            {
                "unified_username": "alice",
                "total_tokens": 30000,
                "message_count": 50,
            },
            {
                "unified_username": "ou_3e479c7f81f8674741d778e8f838f8ed",
                "total_tokens": 15000,
                "message_count": 30,
            },
            {
                "unified_username": "bob",
                "total_tokens": 5000,
                "message_count": 20,
            },
        ]
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {}
        self.daily_stats_repo.get_data_range.return_value = None
        self.usage_repo.get_request_count_total.return_value = 100

        result = self.service.get_batch_analysis()

        # Verify no ou_ prefix users in user_ranking
        user_names = [u["username"] for u in result["user_ranking"]["users"]]
        assert "ou_3e479c7f81f8674741d778e8f838f8ed" not in user_names
        assert "alice" in user_names
        assert "bob" in user_names

    def test_batch_analysis_user_ranking_top_10_limit(self):
        """Test that user_ranking is limited to top 10 after filtering."""
        # Create 12 valid users + 2 ou_ users
        user_data = []
        for i in range(12):
            user_data.append({
                "unified_username": f"user_{i}",
                "total_tokens": 1000 - i * 50,
                "message_count": 10,
            })
        # Add ou_ users that should be filtered
        user_data.append({
            "unified_username": "ou_1234567890abcdef",
            "total_tokens": 99999,
            "message_count": 100,
        })

        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 1000,
            "total_tokens": 50000,
            "total_input_tokens": 30000,
            "total_output_tokens": 20000,
            "unique_tools": 1,
            "unique_hosts": 1,
            "unique_users": 12,
            "unique_days": 1,
        }
        self.daily_stats_repo.get_user_totals.return_value = user_data
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {}
        self.daily_stats_repo.get_data_range.return_value = None
        self.usage_repo.get_request_count_total.return_value = 100

        result = self.service.get_batch_analysis()

        users = result["user_ranking"]["users"]
        # Should have at most 10 users
        assert len(users) <= 10
        # Should not contain the ou_ user
        user_names = [u["username"] for u in users]
        assert "ou_1234567890abcdef" not in user_names


class TestRealConversationCount:
    """Tests for real conversation count replacing approximate sessions."""

    def setup_method(self):
        """Clear cache before each test."""
        get_cache().clear()
        self.daily_stats_repo = MagicMock(spec=DailyStatsRepository)
        self.message_repo = MagicMock(spec=MessageRepository)
        self.usage_repo = MagicMock(spec=UsageRepository)
        self.service = AnalysisService(
            usage_repo=self.usage_repo,
            message_repo=self.message_repo,
            daily_stats_repo=self.daily_stats_repo,
        )
        self.message_repo.count_conversations.return_value = 50

    def _setup_minimal_mocks(self):
        """Setup minimal mock returns for batch analysis."""
        self.daily_stats_repo.get_batch_aggregates.return_value = {
            "total_messages": 5000,
            "total_tokens": 250000,
            "total_input_tokens": 150000,
            "total_output_tokens": 100000,
            "unique_tools": 3,
            "unique_hosts": 2,
            "unique_users": 10,
            "unique_days": 30,
        }
        self.daily_stats_repo.get_user_totals.return_value = []
        self.daily_stats_repo.get_tool_totals.return_value = []
        self.daily_stats_repo.get_daily_totals.return_value = []
        self.daily_stats_repo.get_hourly_totals.return_value = []
        self.daily_stats_repo.get_conversation_stats.return_value = {
            "total_conversations": 90,
            "total_messages": 5000,
            "total_tokens": 250000,
        }
        self.daily_stats_repo.get_data_range.return_value = {
            "min_date": "2024-01-01",
            "max_date": "2024-12-31",
        }
        self.usage_repo.get_request_count_total.return_value = 500

    def test_real_count_replaces_approximate_sessions(self):
        """Test that real conversation count replaces approximate total_sessions."""
        self._setup_minimal_mocks()
        # count_conversations returns 500, while approximate would be 30*3=90
        self.message_repo.count_conversations.return_value = 500

        result = self.service.get_batch_analysis()

        # total_sessions should be the real count (500), not approximation (90)
        assert result["key_metrics"]["total_sessions"] == 500
        # avg_messages_per_session should be 5000 / 500 = 10.0
        assert result["key_metrics"]["avg_messages_per_session"] == 10.0
        # avg_tokens_per_session should be 250000 / 500 = 500.0
        assert result["key_metrics"]["avg_tokens_per_session"] == 500.0

    def test_zero_conversation_count_fallback(self):
        """Test that zero count results in total_sessions=1 and zero averages."""
        self._setup_minimal_mocks()
        self.message_repo.count_conversations.return_value = 0

        result = self.service.get_batch_analysis()

        # total_sessions should fallback to 1
        assert result["key_metrics"]["total_sessions"] == 1
        # averages should be based on total_sessions=1
        assert result["key_metrics"]["avg_messages_per_session"] == 5000
        assert result["key_metrics"]["avg_tokens_per_session"] == 250000

    def test_conversation_count_exception_fallback(self):
        """Test that exception in count_conversations falls back to 0."""
        self._setup_minimal_mocks()
        self.message_repo.count_conversations.side_effect = Exception("timeout")

        result = self.service.get_batch_analysis()

        # Fallback should be 0, which means total_sessions=1
        assert result["key_metrics"]["total_sessions"] == 1

    def test_conversation_stats_total_overridden(self):
        """Test that conversation_stats total_conversations is overridden with real count."""
        self._setup_minimal_mocks()
        # get_conversation_stats returns approximate total_conversations=90
        # but count_conversations returns real count=500
        self.message_repo.count_conversations.return_value = 500

        result = self.service.get_batch_analysis()

        # conversation_stats total_conversations should be overridden to 500
        assert result["conversation_stats"]["total_conversations"] == 500

    def test_count_conversations_date_params(self):
        """Test that count_conversations is called with correct date parameters."""
        self._setup_minimal_mocks()

        result = self.service.get_batch_analysis(
            start_date="2024-01-01",
            end_date="2024-12-31",
            host_name="host1",
        )

        # Verify count_conversations was called with explicit date parameters
        self.message_repo.count_conversations.assert_called_with(
            start_date="2024-01-01",
            end_date="2024-12-31",
            host_name="host1",
        )

    def test_count_conversations_default_dates(self):
        """Test that count_conversations is called even with default dates."""
        self._setup_minimal_mocks()

        result = self.service.get_batch_analysis()

        # count_conversations should have been called (with default dates)
        self.message_repo.count_conversations.assert_called_once()
        call_kwargs = self.message_repo.count_conversations.call_args
        # start_date and end_date should be passed (not None)
        assert call_kwargs[1].get("start_date") is not None
        assert call_kwargs[1].get("end_date") is not None
