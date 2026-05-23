"""Unit tests for UsageAnalytics module."""

from unittest.mock import MagicMock

import pytest

from app.modules.analytics.usage_analytics import (
    Anomaly,
    AnomalyType,
    TrendAnalysis,
    TrendDirection,
    UsageAnalytics,
    UsageReport,
)
from app.utils.cache import get_cache


class TestUsageAnalytics:
    """Test UsageAnalytics."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        analytics = UsageAnalytics(db=mock_db, usage_repo=mock_repo)
        return analytics, mock_db, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_calculate_summary_no_data(self):
        analytics, _, _ = self._make_analytics()
        result = analytics._calculate_summary([])
        assert result["total_tokens"] == 0
        assert result["unique_tools"] == 0
        assert result["peak_day"] is None

    def test_calculate_summary_with_data(self):
        analytics, _, _ = self._make_analytics()
        data = [
            {
                "date": "2026-01-01",
                "tool_name": "qwen",
                "host_name": "h1",
                "tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "requests": 10,
            },
            {
                "date": "2026-01-02",
                "tool_name": "claude",
                "host_name": "h1",
                "tokens": 500,
                "input_tokens": 400,
                "output_tokens": 100,
                "requests": 5,
            },
        ]
        result = analytics._calculate_summary(data)
        assert result["total_tokens"] == 1500
        assert result["unique_tools"] == 2
        assert result["unique_hosts"] == 1
        assert result["peak_day"] == "2026-01-01"
        assert result["peak_tokens"] == 1000

    def test_generate_report_no_data(self):
        analytics, mock_db, _ = self._make_analytics()
        mock_db.fetch_all.return_value = []
        report = analytics.generate_report("2026-01-01", "2026-01-31")
        assert isinstance(report, UsageReport)
        assert report.total_tokens == 0
        assert report.unique_tools == 0

    def test_generate_report_with_data(self):
        analytics, mock_db, _ = self._make_analytics()
        # _get_usage_data query
        usage_data = [
            {
                "date": "2026-01-01",
                "tool_name": "qwen",
                "host_name": "h1",
                "tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "requests": 10,
            },
        ]
        # _get_daily_totals for trends/anomalies
        daily_data = [{"date": "2026-01-01", "tokens": 1000, "requests": 10}]

        def side_effect(query, params=None):
            if "GROUP BY date, tool_name" in query:
                return usage_data
            elif "GROUP BY date" in query and "tool_name" not in query.split("GROUP BY")[0]:
                return daily_data
            elif "GROUP BY tool_name" in query:
                return [
                    {
                        "tool_name": "qwen",
                        "tokens": 1000,
                        "input_tokens": 800,
                        "output_tokens": 200,
                        "requests": 10,
                        "days_active": 1,
                    }
                ]
            elif "GROUP BY host_name" in query:
                return [{"host_name": "h1", "tokens": 1000, "requests": 10, "days_active": 1}]
            return []

        mock_db.fetch_all.side_effect = side_effect
        report = analytics.generate_report(
            "2026-01-01", "2026-01-01", include_trends=False, include_anomalies=False
        )
        assert report.total_tokens == 1000
        assert "qwen" in report.breakdown_by_tool

    def test_get_forecast_insufficient_data(self):
        analytics, mock_db, _ = self._make_analytics()
        mock_db.fetch_all.return_value = [{"date": "2026-01-01", "tokens": 100, "requests": 5}]
        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is False
        assert "reason" in result

    def test_get_forecast_with_data(self):
        analytics, mock_db, _ = self._make_analytics()
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 15)
        ]
        mock_db.fetch_all.return_value = daily_data
        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is True
        assert result["method"] == "moving_average"
        assert "daily_forecast" in result
        assert "total_forecast" in result

    def test_get_efficiency_metrics_no_data(self):
        analytics, mock_db, _ = self._make_analytics()
        mock_db.fetch_all.return_value = []
        result = analytics.get_efficiency_metrics("2026-01-01", "2026-01-31")
        assert result["efficiency_available"] is False

    def test_get_efficiency_metrics_with_data(self):
        analytics, mock_db, _ = self._make_analytics()
        mock_db.fetch_all.return_value = [
            {"tokens": 1000, "input_tokens": 800, "output_tokens": 200, "requests": 10}
        ]
        result = analytics.get_efficiency_metrics("2026-01-01", "2026-01-31")
        assert result["efficiency_available"] is True
        assert result["output_ratio"] == 20.0
        assert result["tokens_per_request"] == 100.0

    def test_trend_analysis_to_dict(self):
        ta = TrendAnalysis(
            metric="tokens",
            direction="up",
            change_percentage=15.5,
            current_value=1000,
            previous_value=865,
            period_days=30,
            confidence=0.8,
        )
        d = ta.to_dict()
        assert d["metric"] == "tokens"
        assert d["direction"] == "up"
        assert d["change_percentage"] == 15.5

    def test_anomaly_to_dict(self):
        a = Anomaly(
            type="spike",
            metric="tokens",
            date="2026-01-15",
            expected_value=100.0,
            actual_value=500.0,
            deviation_percentage=400.0,
            severity="high",
            description="Token usage spike",
        )
        d = a.to_dict()
        assert d["type"] == "spike"
        assert d["severity"] == "high"

    def test_usage_report_to_dict(self):
        report = UsageReport(period_start="2026-01-01", period_end="2026-01-31", total_tokens=5000)
        d = report.to_dict()
        assert d["summary"]["total_tokens"] == 5000
        assert d["period"]["start"] == "2026-01-01"

    def test_trend_direction_enum(self):
        assert TrendDirection.UP.value == "up"
        assert TrendDirection.STABLE.value == "stable"

    def test_anomaly_type_enum(self):
        assert AnomalyType.SPIKE.value == "spike"
        assert AnomalyType.DROP.value == "drop"
