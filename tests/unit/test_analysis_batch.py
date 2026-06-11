"""Unit tests for Analysis Service batch analysis response.

Tests that batch analysis API response includes data_range field.
"""

from concurrent.futures import Future
from unittest.mock import MagicMock, patch, call

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
