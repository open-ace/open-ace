"""Tests for Issue #3030: Token趋势图表选择7天时无数据，但汇总数据显示正常

Verifies that:
1. get_session_trend_by_tool() queries agent_sessions with workspace_type filter
2. get_session_key_metrics() queries agent_sessions for key metrics
3. get_trend_data() merges both daily_stats and agent_sessions
4. get_key_metrics() merges session data into totals
5. Edge cases: empty data, host_name filter, tenant_id filter, date filters
"""

from unittest.mock import MagicMock

import pytest

from app.repositories.usage_repo import UsageRepository
from app.services.analysis_service import AnalysisService
from app.services.usage_service import UsageService
from app.utils.cache import get_cache


def _make_repo(db):
    repo = UsageRepository()
    repo.db = db
    return repo


class TestGetSessionTrendByTool:
    """Test the new get_session_trend_by_tool method."""

    def test_queries_agent_sessions_table(self):
        """Must query agent_sessions, not daily_stats."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")

        sql = db.fetch_all.call_args[0][0]
        assert "FROM agent_sessions" in sql
        assert "daily_stats" not in sql

    def test_filters_workspace_type(self):
        """Must filter to workspace_type IN ('local', 'remote', 'terminal')."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")

        sql = db.fetch_all.call_args[0][0]
        assert "workspace_type IN ('local', 'remote', 'terminal')" in sql

    def test_returns_empty_on_exception(self):
        """Should return empty list on error, not raise."""
        db = MagicMock()
        db.fetch_all.side_effect = Exception("DB error")
        repo = _make_repo(db)

        result = repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")
        assert result == []

    def test_normalizes_tool_name(self):
        """Results should be keyed by normalized tool name."""
        db = MagicMock()
        db.fetch_all.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen-code", "tokens": 5000},
        ]
        repo = _make_repo(db)

        result = repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")

        assert len(result) == 1
        assert result[0]["tool_name"] == "qwen"  # normalize_tool_name("qwen-code") -> "qwen"
        assert result[0]["tokens"] == 5000

    def test_merges_duplicate_tool_names(self):
        """Multiple rows with different tool_name variants should merge after normalization."""
        db = MagicMock()
        db.fetch_all.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen-code", "tokens": 1000},
            {"date": "2026-08-01", "tool_name": "qwen-code-cli", "tokens": 2000},
        ]
        repo = _make_repo(db)

        result = repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")

        # Both normalize to "qwen", same date, should merge
        assert len(result) == 1
        assert result[0]["tokens"] == 3000

    def test_date_filters_applied(self):
        """start_date and end_date should be applied to CAST(created_at AS DATE)."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_trend_by_tool("2026-08-01", "2026-08-31")

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "CAST(created_at AS DATE) >= ?" in sql
        assert "CAST(created_at AS DATE) <= ?" in sql
        assert "2026-08-01" in params
        assert "2026-08-31" in params

    def test_host_name_filter_applied(self):
        """host_name should be applied as a filter."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_trend_by_tool("2026-08-01", "2026-08-31", host_name="my-host")

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "host_name = ?" in sql
        assert "my-host" in params

    def test_tenant_id_filter_applied(self):
        """tenant_id should be applied as a filter."""
        db = MagicMock()
        db.fetch_all.return_value = []
        repo = _make_repo(db)

        repo.get_session_trend_by_tool("2026-08-01", "2026-08-31", tenant_id=42)

        sql = db.fetch_all.call_args[0][0]
        params = db.fetch_all.call_args[0][1]
        assert "tenant_id = ?" in sql
        assert 42 in params


class TestGetSessionKeyMetrics:
    """Test the new get_session_key_metrics method."""

    def test_queries_agent_sessions_table(self):
        """Must query agent_sessions, not daily_messages."""
        db = MagicMock()
        db.fetch_one.return_value = {
            "total_tokens": 10000,
            "total_input_tokens": 8000,
            "total_output_tokens": 2000,
            "total_requests": 100,
            "unique_tools": 3,
            "unique_hosts": 2,
        }
        repo = _make_repo(db)

        repo.get_session_key_metrics("2026-08-01", "2026-08-31")

        sql = db.fetch_one.call_args[0][0]
        assert "FROM agent_sessions" in sql
        assert "daily_messages" not in sql

    def test_filters_workspace_type(self):
        """Must filter to workspace_type IN ('local', 'remote', 'terminal')."""
        db = MagicMock()
        db.fetch_one.return_value = {}
        repo = _make_repo(db)

        repo.get_session_key_metrics("2026-08-01", "2026-08-31")

        sql = db.fetch_one.call_args[0][0]
        assert "workspace_type IN ('local', 'remote', 'terminal')" in sql

    def test_returns_empty_on_exception(self):
        """Should return empty dict on error, not raise."""
        db = MagicMock()
        db.fetch_one.side_effect = Exception("DB error")
        repo = _make_repo(db)

        result = repo.get_session_key_metrics("2026-08-01", "2026-08-31")
        assert result["total_tokens"] == 0
        assert result["total_requests"] == 0

    def test_aggregates_tokens_and_requests(self):
        """Should aggregate total_tokens, input/output, and requests."""
        db = MagicMock()
        db.fetch_one.return_value = {
            "total_tokens": 15000,
            "total_input_tokens": 12000,
            "total_output_tokens": 3000,
            "total_requests": 150,
            "unique_tools": 5,
            "unique_hosts": 3,
        }
        repo = _make_repo(db)

        result = repo.get_session_key_metrics("2026-08-01", "2026-08-31")

        assert result["total_tokens"] == 15000
        assert result["total_input_tokens"] == 12000
        assert result["total_output_tokens"] == 3000
        assert result["total_requests"] == 150
        assert result["unique_tools"] == 5
        assert result["unique_hosts"] == 3


class TestGetTrendDataMerge:
    """Test that get_trend_data merges both data sources."""

    def _make_service(self):
        mock_repo = MagicMock()
        svc = UsageService(usage_repo=mock_repo)
        return svc, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_merges_daily_stats_and_session_data(self):
        """Trend should include data from both daily_stats and agent_sessions."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "claude", "tokens": 10000},
        ]
        mock_repo.get_session_trend_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen", "tokens": 5000},
        ]

        result = svc.get_trend_data("2026-08-01", "2026-08-31")

        assert len(result) == 2
        tools = {r["tool_name"] for r in result}
        assert "claude" in tools
        assert "qwen" in tools

    def test_merges_same_tool_from_both_sources(self):
        """When the same tool appears in both sources, tokens should be summed."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen", "tokens": 10000},
        ]
        mock_repo.get_session_trend_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen", "tokens": 5000},
        ]

        result = svc.get_trend_data("2026-08-01", "2026-08-31")

        assert len(result) == 1
        assert result[0]["tokens"] == 15000

    def test_empty_both_sources(self):
        """When both sources return empty, result should be empty list."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = []
        mock_repo.get_session_trend_by_tool.return_value = []

        result = svc.get_trend_data("2026-08-01", "2026-08-31")
        assert result == []

    def test_only_daily_stats_data(self):
        """When agent_sessions has no data, result should be daily_stats only."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen", "tokens": 10000},
        ]
        mock_repo.get_session_trend_by_tool.return_value = []

        result = svc.get_trend_data("2026-08-01", "2026-08-31")

        assert len(result) == 1
        assert result[0]["tokens"] == 10000

    def test_only_session_data(self):
        """When daily_stats has no data, result should be session data only."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = []
        mock_repo.get_session_trend_by_tool.return_value = [
            {"date": "2026-08-01", "tool_name": "qwen", "tokens": 5000},
        ]

        result = svc.get_trend_data("2026-08-01", "2026-08-31")

        assert len(result) == 1
        assert result[0]["tokens"] == 5000

    def test_passes_filters_to_both_repos(self):
        """All filter parameters should be passed to both repo methods."""
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = []
        mock_repo.get_session_trend_by_tool.return_value = []

        svc.get_trend_data(
            "2026-08-01",
            "2026-08-31",
            host_name="my-host",
            tenant_id=42,
        )

        mock_repo.get_daily_by_tool.assert_called_once_with(
            "2026-08-01", "2026-08-31", "my-host", 42
        )
        mock_repo.get_session_trend_by_tool.assert_called_once_with(
            "2026-08-01", "2026-08-31", "my-host", 42
        )


class TestGetKeyMetricsMerge:
    """Test that get_key_metrics merges session data into totals."""

    def _make_service(self):
        mock_usage_repo = MagicMock()
        mock_message_repo = MagicMock()
        mock_daily_stats_repo = MagicMock()
        svc = AnalysisService(
            usage_repo=mock_usage_repo,
            message_repo=mock_message_repo,
            daily_stats_repo=mock_daily_stats_repo,
        )
        return svc, mock_usage_repo, mock_message_repo

    def setup_method(self):
        get_cache().clear()

    def test_merges_session_tokens_into_totals(self):
        """Key metrics should include session tokens in totals."""
        svc, mock_usage_repo, mock_message_repo = self._make_service()
        mock_usage_repo.get_daily_range.return_value = [
            {
                "tokens_used": 10000,
                "input_tokens": 8000,
                "output_tokens": 2000,
                "request_count": 50,
            },
        ]
        mock_message_repo.get_user_token_totals.return_value = []
        mock_message_repo.get_tool_token_totals.return_value = []
        mock_message_repo.get_conversation_stats_summary.return_value = {}
        mock_usage_repo.get_session_key_metrics.return_value = {
            "total_tokens": 5000,
            "total_input_tokens": 4000,
            "total_output_tokens": 1000,
            "total_requests": 20,
            "unique_tools": 3,
            "unique_hosts": 2,
        }

        result = svc.get_key_metrics("2026-08-01", "2026-08-31")

        # 10000 + 5000 = 15000
        assert result["total_tokens"] == 15000
        assert result["total_input_tokens"] == 12000
        assert result["total_output_tokens"] == 3000
        assert result["total_requests"] == 70

    def test_session_data_only(self):
        """When daily_messages is empty, session data should still be included."""
        svc, mock_usage_repo, mock_message_repo = self._make_service()
        mock_usage_repo.get_daily_range.return_value = []
        mock_message_repo.get_user_token_totals.return_value = []
        mock_message_repo.get_tool_token_totals.return_value = []
        mock_message_repo.get_conversation_stats_summary.return_value = {}
        mock_usage_repo.get_session_key_metrics.return_value = {
            "total_tokens": 5000,
            "total_input_tokens": 4000,
            "total_output_tokens": 1000,
            "total_requests": 20,
            "unique_tools": 3,
            "unique_hosts": 2,
        }

        result = svc.get_key_metrics("2026-08-01", "2026-08-31")

        assert result["total_tokens"] == 5000
        assert result["total_requests"] == 20

    def test_passes_filters_to_session_repo(self):
        """All filter parameters should be passed to get_session_key_metrics."""
        svc, mock_usage_repo, mock_message_repo = self._make_service()
        mock_usage_repo.get_daily_range.return_value = []
        mock_message_repo.get_user_token_totals.return_value = []
        mock_message_repo.get_tool_token_totals.return_value = []
        mock_message_repo.get_conversation_stats_summary.return_value = {}
        mock_usage_repo.get_session_key_metrics.return_value = {
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_requests": 0,
            "unique_tools": 0,
            "unique_hosts": 0,
        }

        svc.get_key_metrics(
            start_date="2026-08-01",
            end_date="2026-08-31",
            host_name="my-host",
            tenant_id=42,
        )

        mock_usage_repo.get_session_key_metrics.assert_called_once_with(
            start_date="2026-08-01",
            end_date="2026-08-31",
            host_name="my-host",
            tenant_id=42,
        )
