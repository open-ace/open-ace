"""Unit tests for AnalysisService."""

from unittest.mock import MagicMock

import pytest

from app.services.analysis_service import AnalysisService
from app.utils.cache import get_cache


class TestAnalysisService:
    """Test AnalysisService business logic."""

    def _make_service(self):
        mock_usage_repo = MagicMock()
        mock_message_repo = MagicMock()
        mock_daily_stats = MagicMock()
        svc = AnalysisService(
            usage_repo=mock_usage_repo,
            message_repo=mock_message_repo,
            daily_stats_repo=mock_daily_stats,
        )
        return svc, mock_usage_repo, mock_message_repo, mock_daily_stats

    def setup_method(self):
        get_cache().clear()

    def test_get_key_metrics_with_usage_data(self):
        svc, mock_usage, mock_msg, mock_stats = self._make_service()
        mock_usage.get_daily_range.return_value = [
            {
                "date": "2026-05-23",
                "tool_name": "qwen",
                "tokens_used": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "request_count": 5,
                "host_name": "h1",
            }
        ]
        mock_msg.get_user_token_totals.return_value = [
            {
                "total_tokens": 1000,
                "message_count": 5,
                "total_input_tokens": 800,
                "total_output_tokens": 200,
            }
        ]
        mock_msg.get_tool_token_totals.return_value = [{"tool_name": "qwen", "total_tokens": 1000}]
        result = svc.get_key_metrics("2026-05-01", "2026-05-23")
        assert result["total_tokens"] == 1000
        assert result["total_requests"] == 5
        assert result["unique_tools"] == 1

    def test_get_key_metrics_no_data(self):
        svc, mock_usage, mock_msg, _ = self._make_service()
        mock_usage.get_daily_range.return_value = []
        mock_msg.get_user_token_totals.return_value = []
        mock_msg.get_tool_token_totals.return_value = []
        result = svc.get_key_metrics("2026-05-01", "2026-05-23")
        assert result["total_tokens"] == 0
        assert result["unique_tools"] == 1

    def test_get_peak_usage(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_daily_token_totals.return_value = [
            {"date": "2026-05-01", "total_tokens": 100},
            {"date": "2026-05-02", "total_tokens": 500},
            {"date": "2026-05-03", "total_tokens": 200},
        ]
        mock_msg.get_hourly_usage.return_value = [
            {"hour": 10, "tokens": 300, "requests": 10},
            {"hour": 14, "tokens": 500, "requests": 20},
        ]
        result = svc.get_peak_usage("2026-05-01", "2026-05-03")
        assert result["peak_day"] == "2026-05-02"
        assert result["peak_tokens"] == 500
        assert len(result["peak_days"]) <= 5

    def test_get_peak_usage_no_data(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_daily_token_totals.return_value = []
        result = svc.get_peak_usage("2026-05-01", "2026-05-03")
        assert result["peak_days"] == []

    def test_get_user_ranking(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_user_token_totals.return_value = [
            {"sender_name": "user1", "total_tokens": 5000, "message_count": 50},
            {"sender_name": "user2", "total_tokens": 3000, "message_count": 30},
        ]
        result = svc.get_user_ranking("2026-05-01", "2026-05-23")
        assert len(result["users"]) == 2
        assert result["users"][0]["tokens"] == 5000

    def test_get_user_ranking_cleans_machine_suffix(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_user_token_totals.return_value = [
            {
                "sender_name": "testuser-MacBook-Pro.local",
                "total_tokens": 1000,
                "message_count": 10,
            },
        ]
        result = svc.get_user_ranking("2026-05-01", "2026-05-23")
        assert result["users"][0]["username"] == "testuser"

    def test_get_conversation_stats(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_conversation_history.return_value = [
            {"message_count": 10, "total_tokens": 500},
            {"message_count": 5, "total_tokens": 200},
        ]
        result = svc.get_conversation_stats("2026-05-01", "2026-05-23")
        assert result["total_conversations"] == 2
        assert result["total_messages"] == 15

    def test_get_tool_comparison(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_tool_token_totals.return_value = [
            {
                "tool_name": "qwen-code",
                "total_tokens": 5000,
                "message_count": 50,
                "total_input_tokens": 4000,
                "total_output_tokens": 1000,
            },
            {
                "tool_name": "claude",
                "total_tokens": 3000,
                "message_count": 30,
                "total_input_tokens": 2000,
                "total_output_tokens": 1000,
            },
        ]
        result = svc.get_tool_comparison("2026-05-01", "2026-05-23")
        assert len(result["tools"]) == 2
        # "qwen-code" normalizes to "qwen", sorted by tokens desc
        assert result["tools"][0]["tool_name"] == "qwen"

    def test_get_user_segmentation(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_user_token_totals.return_value = [
            {"total_tokens": 20000},
            {"total_tokens": 5000},
            {"total_tokens": 500},
        ]
        result = svc.get_user_segmentation("2026-05-01", "2026-05-23")
        assert result["high"] == 1
        assert result["medium"] == 1
        assert result["low"] == 1

    def test_detect_anomalies(self):
        svc, _, mock_msg, _ = self._make_service()
        normal = [{"date": f"2026-05-{i:02d}", "total_tokens": 100} for i in range(1, 11)]
        normal.append({"date": "2026-05-11", "total_tokens": 5000})
        mock_msg.get_daily_token_totals.return_value = normal
        result = svc.detect_anomalies("2026-05-01", "2026-05-11")
        assert len(result["anomalies"]) >= 1
        assert result["summary"]["total"] >= 1

    def test_detect_anomalies_insufficient_data(self):
        svc, _, mock_msg, _ = self._make_service()
        mock_msg.get_daily_token_totals.return_value = [{"date": "2026-05-01", "total_tokens": 100}]
        result = svc.detect_anomalies("2026-05-01", "2026-05-01")
        assert result["anomalies"] == []
        assert result["summary"]["total"] == 0

    def test_get_recommendations_with_data(self):
        svc, mock_usage, _, _ = self._make_service()
        mock_usage.get_daily_range.return_value = [
            {
                "tokens_used": 10000,
                "input_tokens": 9000,
                "output_tokens": 1000,
                "tool_name": "qwen",
            },
        ]
        result = svc.get_recommendations()
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_get_recommendations_no_data(self):
        svc, mock_usage, _, _ = self._make_service()
        mock_usage.get_daily_range.return_value = []
        result = svc.get_recommendations()
        assert len(result) == 1
        assert result[0]["type"] == "info"
