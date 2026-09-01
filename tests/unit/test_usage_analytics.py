"""Unit tests for UsageAnalytics module."""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.analytics.usage_analytics import (
    Anomaly,
    AnomalyType,
    TrendAnalysis,
    TrendDirection,
    UsageAnalytics,
    UsageReport,
    calculate_moving_average,
    MISSING_DAYS_THRESHOLD_DEGRADED,
    MISSING_DAYS_THRESHOLD_UNAVAILABLE,
)
from app.utils.cache import get_cache
from app.utils.datetime_utils import (
    ForecastWindow,
    generate_date_spine,
    get_business_date,
    get_forecast_window,
)


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
        # Mock fetch_one for first activity date query
        mock_db.fetch_one.return_value = {"first_date": None}
        # Mock fetch_all for daily stats - return only 1 day
        mock_db.fetch_all.return_value = [{"date": "2026-01-01", "tokens": 100, "requests": 5}]
        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is False
        assert "reason" in result

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_get_forecast_with_data(self, mock_get_business_date):
        mock_get_business_date.return_value = "2026-01-15"
        analytics, mock_db, _ = self._make_analytics()
        # Mock fetch_one for first activity date query
        mock_db.fetch_one.return_value = {"first_date": "2026-01-01"}
        # Mock fetch_all for daily stats - return 7 days of data
        mock_db.fetch_all.return_value = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(8, 15)
        ]
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


class TestDatetimeUtils:
    """Test datetime utilities for Issue #3244."""

    def test_get_business_date(self):
        """Test business date returns UTC date string."""
        result = get_business_date()
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD format

    def test_get_forecast_window_basic(self):
        """Test basic forecast window calculation."""
        window = get_forecast_window("2026-08-31", days=7)
        assert window.start_date == "2026-08-24"
        assert window.end_date == "2026-08-30"
        assert window.days == 7

    def test_get_forecast_window_with_first_activity(self):
        """Test forecast window bounded by first activity date."""
        window = get_forecast_window("2026-08-31", days=7, first_activity_date="2026-08-28")
        assert window.start_date == "2026-08-28"
        assert window.end_date == "2026-08-30"
        assert window.days == 3

    def test_get_forecast_window_first_activity_before_window(self):
        """Test that first activity date before window start is ignored."""
        window = get_forecast_window("2026-08-31", days=7, first_activity_date="2026-08-01")
        assert window.start_date == "2026-08-24"
        assert window.days == 7

    def test_generate_date_spine(self):
        """Test date spine generation."""
        dates = generate_date_spine("2026-08-01", "2026-08-03")
        assert dates == ["2026-08-01", "2026-08-02", "2026-08-03"]

    def test_generate_date_spine_single_day(self):
        """Test date spine with single day."""
        dates = generate_date_spine("2026-08-01", "2026-08-01")
        assert dates == ["2026-08-01"]


class TestMovingAverage:
    """Test moving average calculation for Issue #3244."""

    def test_calculate_moving_average_basic(self):
        """Test basic moving average calculation."""
        values = [100, 200, 150, 180, 220, 190, 210]
        result = calculate_moving_average(values, 7)
        assert result is not None
        assert abs(result - sum(values) / 7) < 0.01

    def test_calculate_moving_average_insufficient_data(self):
        """Test moving average with insufficient data."""
        values = [100, 200]
        result = calculate_moving_average(values, 7)
        assert result is None

    def test_calculate_moving_average_exact_window(self):
        """Test moving average with exact window size."""
        values = [100, 100, 100, 100, 100, 100, 100]
        result = calculate_moving_average(values, 7)
        assert result == 100.0

    def test_calculate_moving_average_uses_last_n(self):
        """Test that moving average uses only last n values."""
        values = [1000, 1000, 100, 100, 100, 100, 100, 100, 100]
        result = calculate_moving_average(values, 7)
        assert result == 100.0  # Only last 7 values


class TestForecastAlgorithm:
    """Test forecast algorithm fixes for Issue #3244."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        analytics = UsageAnalytics(db=mock_db, usage_repo=mock_repo)
        return analytics, mock_db, mock_repo

    def setup_method(self):
        get_cache().clear()

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_continuous_7_days(self, mock_get_business_date):
        """Test forecast with continuous 7 days of data."""
        # Mock business date to be 2026-08-31
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        # Mock first activity date query
        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": "2026-08-01"}
            return None

        # Mock fetch_all for daily stats - return 7 days of continuous data
        def mock_fetch_all(query, params=None):
            # Return 7 days: 2026-08-24 to 2026-08-30
            return [
                {"date": f"2026-08-{24+i}", "tokens": 100, "requests": 10}
                for i in range(7)
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is True
        assert result["quality"] == "normal"
        assert result["confidence"] == 0.7
        assert result["history_window"]["days"] == 7
        assert result["history_window"]["missing_days"] == 0
        assert result["algorithm_version"] == "v2"

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_missing_days_degraded(self, mock_get_business_date):
        """Test forecast with missing days results in degraded quality."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": "2026-08-01"}
            return None

        # Return only 5 days of data (2 missing: 26, 27)
        def mock_fetch_all(query, params=None):
            return [
                {"date": "2026-08-24", "tokens": 100, "requests": 10},
                {"date": "2026-08-25", "tokens": 100, "requests": 10},
                {"date": "2026-08-28", "tokens": 100, "requests": 10},  # Missing 26, 27
                {"date": "2026-08-29", "tokens": 100, "requests": 10},
                {"date": "2026-08-30", "tokens": 100, "requests": 10},
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is True
        assert result["quality"] == "degraded"
        assert result["confidence"] == 0.5
        assert result["history_window"]["missing_days"] == 2

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_missing_days_unavailable(self, mock_get_business_date):
        """Test forecast with too many missing days is unavailable."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": "2026-08-01"}
            return None

        # Return only 3 days of data (4 missing)
        def mock_fetch_all(query, params=None):
            return [
                {"date": "2026-08-24", "tokens": 100, "requests": 10},
                {"date": "2026-08-28", "tokens": 100, "requests": 10},
                {"date": "2026-08-30", "tokens": 100, "requests": 10},
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is False
        assert "missing days" in result["reason"].lower()
        assert result["history_window"]["missing_days"] == 4

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_new_user_too_new(self, mock_get_business_date):
        """Test forecast for new user with insufficient history."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                # First activity only 3 days ago
                return {"first_date": "2026-08-28"}
            return None

        def mock_fetch_all(query, params=None):
            return [
                {"date": "2026-08-28", "tokens": 100, "requests": 10},
                {"date": "2026-08-29", "tokens": 100, "requests": 10},
                {"date": "2026-08-30", "tokens": 100, "requests": 10},
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is False
        assert "too new" in result["reason"].lower()
        assert result["history_window"]["days"] == 3

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_excludes_current_day(self, mock_get_business_date):
        """Test that forecast excludes the incomplete current day."""
        # Business date is 2026-08-31
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": "2026-08-01"}
            return None

        # Return data for days 24-30 (7 days before 31)
        def mock_fetch_all(query, params=None):
            return [
                {"date": f"2026-08-{24+i}", "tokens": 100, "requests": 10}
                for i in range(7)
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        # The window should end on 2026-08-30, not 2026-08-31
        assert result["forecast_available"] is True
        assert result["history_window"]["end_date"] == "2026-08-30"

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_zero_usage_days(self, mock_get_business_date):
        """Test forecast with zero usage days."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": "2026-08-01"}
            return None

        # Return 7 days with zero usage
        def mock_fetch_all(query, params=None):
            return [
                {"date": f"2026-08-{24+i}", "tokens": 0, "requests": 0}
                for i in range(7)
            ]

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is True
        assert result["daily_forecast"]["tokens"] == 0
        assert result["daily_forecast"]["requests"] == 0

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_tenant_isolation(self, mock_get_business_date):
        """Test forecast respects tenant isolation."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                if params and params[0] == 123:
                    return {"first_date": "2026-08-01"}
                return None
            return None

        # Track which queries were called
        called_params = []

        def mock_fetch_all(query, params=None):
            called_params.append(params)
            if "tenant_id" in query and params and len(params) == 3:
                # Tenant-specific query
                return [
                    {"date": f"2026-08-{24+i}", "tokens": 100, "requests": 10}
                    for i in range(7)
                ]
            return []

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7, tenant_id=123)
        assert result["forecast_available"] is True

    @patch("app.modules.analytics.usage_analytics.get_business_date")
    def test_forecast_no_data_at_all(self, mock_get_business_date):
        """Test forecast when database has no data."""
        mock_get_business_date.return_value = "2026-08-31"

        analytics, mock_db, _ = self._make_analytics()

        def mock_fetch_one(query, params=None):
            if "MIN(date)" in query:
                return {"first_date": None}
            return None

        def mock_fetch_all(query, params=None):
            return []

        mock_db.fetch_one = mock_fetch_one
        mock_db.fetch_all = mock_fetch_all

        result = analytics.get_forecast(days=7)
        assert result["forecast_available"] is False
