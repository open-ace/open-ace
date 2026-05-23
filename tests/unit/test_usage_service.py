"""Unit tests for UsageService."""

from unittest.mock import MagicMock

import pytest

from app.services.usage_service import UsageService
from app.utils.cache import get_cache


class TestUsageService:
    """Test UsageService business logic."""

    def _make_service(self):
        mock_repo = MagicMock()
        svc = UsageService(usage_repo=mock_repo)
        return svc, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_get_today_usage_empty(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_usage_rows_by_date.return_value = []
        result = svc.get_today_usage()
        assert result == []

    def test_get_today_usage_with_data(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_usage_rows_by_date.return_value = [
            {
                "tool_name": "qwen-code",
                "tokens_used": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "cache_tokens": 50,
                "request_count": 10,
                "host_name": "h1",
                "models_used": '["qwen-max"]',
            }
        ]
        result = svc.get_today_usage()
        assert len(result) == 1
        # normalize_tool_name("qwen-code") -> "qwen"
        assert result[0]["tool_name"] == "qwen"
        assert result[0]["tokens_used"] == 1000

    def test_get_today_usage_merges_tools(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_usage_rows_by_date.return_value = [
            {
                "tool_name": "qwen-code",
                "tokens_used": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "cache_tokens": 0,
                "request_count": 5,
                "host_name": "h1",
                "models_used": None,
            },
            {
                "tool_name": "qwen-code-cli",
                "tokens_used": 500,
                "input_tokens": 400,
                "output_tokens": 100,
                "cache_tokens": 0,
                "request_count": 3,
                "host_name": "h1",
                "models_used": None,
            },
        ]
        result = svc.get_today_usage()
        # Both normalize to "qwen", should be merged
        assert len(result) == 1
        assert result[0]["tool_name"] == "qwen"
        assert result[0]["tokens_used"] == 1500
        assert result[0]["request_count"] == 8

    def test_get_usage_summary(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_summary_by_tool.return_value = {"qwen": {"total": 1000}}
        result = svc.get_usage_summary()
        assert "qwen" in result

    def test_get_tool_usage(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_usage_by_tool.return_value = [{"date": "2026-01-01", "tokens": 100}]
        result = svc.get_tool_usage("qwen", days=7)
        assert len(result) == 1

    def test_get_date_usage(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_usage_by_date.return_value = [{"tokens_used": 100}]
        result = svc.get_date_usage("2026-01-01")
        assert len(result) == 1

    def test_get_range_usage(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_range.return_value = [{"date": "2026-01-01", "tokens": 100}]
        result = svc.get_range_usage("2026-01-01", "2026-01-31")
        assert len(result) == 1

    def test_get_all_tools(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_all_tools.return_value = ["qwen", "claude"]
        result = svc.get_all_tools()
        assert len(result) == 2

    def test_get_all_hosts(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_all_hosts.return_value = ["host1", "host2"]
        result = svc.get_all_hosts()
        assert len(result) == 2

    def test_save_usage(self):
        svc, mock_repo = self._make_service()
        mock_repo.save_usage.return_value = True
        result = svc.save_usage(
            date="2026-01-01",
            tool_name="qwen",
            tokens_used=1000,
            input_tokens=800,
            output_tokens=200,
        )
        assert result is True

    def test_get_trend_data(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_daily_by_tool.return_value = [
            {"date": "2026-01-01", "tool_name": "qwen", "tokens": 100}
        ]
        result = svc.get_trend_data("2026-01-01", "2026-01-31")
        assert len(result) == 1
