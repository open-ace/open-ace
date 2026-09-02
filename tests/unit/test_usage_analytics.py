"""Unit tests for UsageAnalytics module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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

    def test_calculate_summary_normalizes_date_object(self):
        """PostgreSQL returns datetime.date object; should normalize to YYYY-MM-DD string."""
        from datetime import date

        analytics, _, _ = self._make_analytics()
        data = [
            {
                "date": date(2026, 1, 1),  # PostgreSQL returns datetime.date
                "tool_name": "qwen",
                "host_name": "h1",
                "tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "requests": 10,
            },
            {
                "date": date(2026, 1, 2),
                "tool_name": "claude",
                "host_name": "h1",
                "tokens": 500,
                "input_tokens": 400,
                "output_tokens": 100,
                "requests": 5,
            },
        ]
        result = analytics._calculate_summary(data)
        # peak_day should be string, not datetime.date object
        assert result["peak_day"] == "2026-01-01"
        assert isinstance(result["peak_day"], str)

    def test_calculate_summary_with_missing_days(self):
        """Daily averages should use full period, not just active days.

        Issue #3256: When a 31-day period has only 19 active days,
        the average should be total / 31, not total / 19.
        """
        analytics, _, _ = self._make_analytics()
        # 19 days of data in a 31-day period (2026-01-01 to 2026-01-31)
        data = [
            {
                "date": f"2026-01-{i:02d}",
                "tool_name": "qwen",
                "host_name": "h1",
                "tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "requests": 10,
            }
            for i in range(1, 20)  # Days 1-19 (19 active days)
        ]
        total_tokens = 19 * 1000  # 19000 tokens total

        # Without date parameters: should use active days (19)
        result_no_dates = analytics._calculate_summary(data)
        assert result_no_dates["daily_average_tokens"] == total_tokens / 19

        # With date parameters: should use full period (31 days)
        result_with_dates = analytics._calculate_summary(
            data, start_date="2026-01-01", end_date="2026-01-31"
        )
        assert result_with_dates["daily_average_tokens"] == total_tokens / 31

    def test_calculate_summary_single_day_period(self):
        """Single day period should give same result regardless of method."""
        analytics, _, _ = self._make_analytics()
        data = [
            {
                "date": "2026-01-15",
                "tool_name": "qwen",
                "host_name": "h1",
                "tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "requests": 10,
            }
        ]

        # Both methods should give same result for single day
        result_no_dates = analytics._calculate_summary(data)
        result_with_dates = analytics._calculate_summary(
            data, start_date="2026-01-15", end_date="2026-01-15"
        )

        assert result_no_dates["daily_average_tokens"] == 1000
        assert result_with_dates["daily_average_tokens"] == 1000

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

    def test_get_forecast_invalid_days_negative(self):
        """Test get_forecast rejects negative days."""
        analytics, _, _ = self._make_analytics()
        with pytest.raises(ValueError, match="days must be an integer between 1 and 90"):
            analytics.get_forecast(days=-1)

    def test_get_forecast_invalid_days_zero(self):
        """Test get_forecast rejects zero days."""
        analytics, _, _ = self._make_analytics()
        with pytest.raises(ValueError, match="days must be an integer between 1 and 90"):
            analytics.get_forecast(days=0)

    def test_get_forecast_invalid_days_exceeds_max(self):
        """Test get_forecast rejects days > 90."""
        analytics, _, _ = self._make_analytics()
        with pytest.raises(ValueError, match="days must be an integer between 1 and 90"):
            analytics.get_forecast(days=91)

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


class TestBacktestWape:
    """Test backtest WAPE calculation."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo)

    def setup_method(self):
        get_cache().clear()

    def test_wape_stable_data(self):
        """Stable data should have low WAPE."""
        analytics = self._make_analytics()
        # Create 20 days of stable data (100 tokens each)
        historical_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 21)
        ]
        wape = analytics._calculate_backtest_wape(historical_data)
        assert wape is not None
        assert wape < 0.1  # Less than 10% error for stable data

    def test_wape_high_volatility(self):
        """High volatility data should have higher WAPE."""
        analytics = self._make_analytics()
        # Create 20 days of volatile data
        historical_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100 + (i % 3) * 200, "requests": 10}
            for i in range(1, 21)
        ]
        wape = analytics._calculate_backtest_wape(historical_data)
        assert wape is not None
        assert wape > 0.1  # Higher error for volatile data

    def test_wape_insufficient_data(self):
        """Insufficient data should return None."""
        analytics = self._make_analytics()
        historical_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10}
            for i in range(1, 10)  # Only 9 days
        ]
        wape = analytics._calculate_backtest_wape(historical_data)
        assert wape is None

    def test_wape_all_zeros(self):
        """All zero values should return None."""
        analytics = self._make_analytics()
        historical_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 0, "requests": 0} for i in range(1, 21)
        ]
        wape = analytics._calculate_backtest_wape(historical_data)
        assert wape is None


class TestHorizonDecay:
    """Test horizon decay calculation."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo)

    def test_decay_7_days(self):
        """7-day forecast should have no decay."""
        analytics = self._make_analytics()
        base_wape = 0.15
        adjusted = analytics._apply_horizon_decay(base_wape, 7)
        assert adjusted == base_wape

    def test_decay_30_days(self):
        """30-day forecast should have significant decay."""
        analytics = self._make_analytics()
        base_wape = 0.15
        adjusted = analytics._apply_horizon_decay(base_wape, 30)
        # Decay factor: 1 + 0.02 * (30 - 7) = 1.46
        assert abs(adjusted - base_wape * 1.46) < 0.001

    def test_decay_14_days(self):
        """14-day forecast should have moderate decay."""
        analytics = self._make_analytics()
        base_wape = 0.15
        adjusted = analytics._apply_horizon_decay(base_wape, 14)
        # Decay factor: 1 + 0.02 * (14 - 7) = 1.14
        assert abs(adjusted - base_wape * 1.14) < 0.001


class TestOutlierDetection:
    """Test outlier detection using MAD."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo)

    def test_no_outliers(self):
        """Stable data should have no outliers."""
        analytics = self._make_analytics()
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 15)
        ]
        count, ratio = analytics._detect_outliers(daily_data)
        assert count == 0
        assert ratio == 0.0

    def test_single_outlier(self):
        """Single spike should be detected when MAD > 0."""
        analytics = self._make_analytics()
        # Create data with variation so MAD > 0
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100 + (i % 3) * 10, "requests": 10}
            for i in range(1, 15)
        ]
        # Add one large spike (10x the max value)
        daily_data[7]["tokens"] = 3000
        count, ratio = analytics._detect_outliers(daily_data)
        assert count >= 1
        assert ratio > 0.0

    def test_mad_zero(self):
        """MAD=0 (identical values) should return no outliers."""
        analytics = self._make_analytics()
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 15)
        ]
        # All values are 100, MAD=0
        count, ratio = analytics._detect_outliers(daily_data)
        assert count == 0
        assert ratio == 0.0


class TestQualityAssessment:
    """Test quality assessment."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo)

    def test_quality_level(self):
        """Low WAPE and no missing data should give quality level."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.08, sample_days=30, missing_days=0, outlier_ratio=0.0
        )
        assert level == "quality"
        assert confidence == 0.9

    def test_satisfactory_level(self):
        """Moderate WAPE should give satisfactory level."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.15, sample_days=30, missing_days=2, outlier_ratio=0.0
        )
        assert level == "satisfactory"
        assert confidence == 0.7

    def test_fair_level(self):
        """Higher WAPE should give fair level."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.25, sample_days=30, missing_days=4, outlier_ratio=0.0
        )
        assert level == "fair"
        assert confidence == 0.5

    def test_poor_level(self):
        """High WAPE should give poor level."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.40, sample_days=30, missing_days=0, outlier_ratio=0.0
        )
        assert level == "poor"
        assert confidence == 0.3

    def test_unavailable_insufficient_sample(self):
        """Insufficient sample should give unavailable."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.15, sample_days=5, missing_days=0, outlier_ratio=0.0
        )
        assert level == "unavailable"
        assert confidence is None

    def test_unavailable_null_wape(self):
        """Null WAPE should give unavailable."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=None, sample_days=30, missing_days=0, outlier_ratio=0.0
        )
        assert level == "unavailable"
        assert confidence is None

    def test_outlier_downgrade(self):
        """High outlier ratio should downgrade quality."""
        analytics = self._make_analytics()
        level, desc, confidence = analytics._assess_forecast_quality(
            adjusted_wape=0.08, sample_days=30, missing_days=0, outlier_ratio=0.15
        )
        assert level == "satisfactory"  # Downgraded from quality
        assert confidence == 0.7


class TestMissingDays:
    """Test missing days detection."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo)

    def test_no_missing_days(self):
        """Continuous data should have no missing days."""
        analytics = self._make_analytics()
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 31)
        ]
        missing = analytics._count_missing_days(daily_data, 30)
        assert missing == 0

    def test_some_missing_days(self):
        """Gaps should be counted as missing."""
        analytics = self._make_analytics()
        # Only 27 days of data for expected 30
        daily_data = [
            {"date": f"2026-01-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 28)
        ]
        missing = analytics._count_missing_days(daily_data, 30)
        assert missing == 3


class TestHistoricalDataForBacktest:
    """Test historical data extraction for backtest."""

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        analytics = UsageAnalytics(db=mock_db, usage_repo=mock_repo)
        return analytics, mock_db

    def test_excludes_today(self):
        """Today's data should be excluded."""
        analytics, mock_db = self._make_analytics()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Mock data including today
        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 100, "requests": 10} for i in range(1, 31)
        ]
        # Override last entry to be today
        daily_data[-1]["date"] = today
        mock_db.fetch_all.return_value = daily_data

        historical, sample_days = analytics._get_historical_data_for_backtest(
            "2026-08-01", "2026-08-31"
        )

        # Today should be excluded
        assert all(d["date"] < today for d in historical)
        assert sample_days == len(historical)


class TestForecastQualityMetrics:
    """Test complete forecast with quality metrics.

    Issue #3244: Quality metrics now based on missing days ratio.
    """

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo), mock_db

    def setup_method(self):
        get_cache().clear()

    def test_forecast_includes_quality_metrics(self):
        """Forecast should include quality metrics."""
        analytics, mock_db = self._make_analytics()

        # Mock 7 days of continuous historical data
        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 1000, "requests": 50} for i in range(24, 31)
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        assert result["forecast_available"] is True
        assert "quality_level" in result
        assert "quality_description" in result
        assert "quality_metrics" in result
        assert "sample_days" in result["quality_metrics"]
        assert "missing_days" in result["quality_metrics"]

    def test_forecast_backward_compatible(self):
        """Forecast should include backward-compatible confidence."""
        analytics, mock_db = self._make_analytics()

        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 1000, "requests": 50} for i in range(24, 31)
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        assert "confidence" in result
        assert "_deprecated_note" in result

    def test_forecast_with_missing_days_degraded_quality(self):
        """Forecast with 2+ missing days should have degraded quality."""
        analytics, mock_db = self._make_analytics()

        # 5 days of data out of 7 day window (2 missing)
        daily_data = [
            {"date": "2026-08-24", "tokens": 1000, "requests": 50},
            {"date": "2026-08-25", "tokens": 1000, "requests": 50},
            # 2026-08-26 missing
            {"date": "2026-08-27", "tokens": 1000, "requests": 50},
            # 2026-08-28 missing
            {"date": "2026-08-29", "tokens": 1000, "requests": 50},
            {"date": "2026-08-30", "tokens": 1000, "requests": 50},
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        assert result["forecast_available"] is True
        assert result["quality_level"] == "fair"
        assert result["quality_metrics"]["missing_days"] == 2


class TestIssue3244ContinuousCalendarDays:
    """Test forecast with continuous calendar days.

    Issue #3244: Forecast algorithm should use continuous calendar days,
    exclude incomplete current day, and return history window metadata.
    """

    def _make_analytics(self):
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return UsageAnalytics(db=mock_db, usage_repo=mock_repo), mock_db

    def setup_method(self):
        get_cache().clear()

    def test_forecast_uses_continuous_calendar_days(self):
        """Forecast should use continuous calendar days, not just active days."""
        analytics, mock_db = self._make_analytics()

        # Mock data with gaps (missing 2026-08-25)
        daily_data = [
            {"date": "2026-08-24", "tokens": 100, "requests": 10},
            # 2026-08-25 is missing
            {"date": "2026-08-26", "tokens": 100, "requests": 10},
            {"date": "2026-08-27", "tokens": 100, "requests": 10},
            {"date": "2026-08-28", "tokens": 100, "requests": 10},
            {"date": "2026-08-29", "tokens": 100, "requests": 10},
            {"date": "2026-08-30", "tokens": 100, "requests": 10},
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        # Should fill missing day with zero
        assert result["forecast_available"] is True
        assert result["quality_metrics"]["missing_days"] == 1
        assert result["history_window"]["total_days"] == 7

    def test_forecast_excludes_current_day(self):
        """Forecast should exclude the incomplete current day."""
        analytics, mock_db = self._make_analytics()

        # Data includes the current day (2026-08-31)
        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 100, "requests": 10} for i in range(24, 32)
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        # Window should be 2026-08-24 to 2026-08-30 (excluding 2026-08-31)
        assert result["history_window"]["end_date"] == "2026-08-30"
        assert result["history_window"]["start_date"] == "2026-08-24"

    def test_forecast_returns_history_window_metadata(self):
        """Forecast should return history window metadata."""
        analytics, mock_db = self._make_analytics()

        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 100, "requests": 10} for i in range(24, 31)
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        assert "history_window" in result
        assert "start_date" in result["history_window"]
        assert "end_date" in result["history_window"]
        assert "total_days" in result["history_window"]
        assert "missing_days" in result["history_window"]

    def test_forecast_with_explicit_business_date(self):
        """Forecast with explicit business_date should use it for cache key."""
        analytics, mock_db = self._make_analytics()

        daily_data = [
            {"date": f"2026-08-{i:02d}", "tokens": 100, "requests": 10} for i in range(24, 31)
        ]
        mock_db.fetch_all.return_value = daily_data

        # Call with same business_date twice - should hit cache
        result1 = analytics.get_forecast(days=7, business_date="2026-08-31")
        result2 = analytics.get_forecast(days=7, business_date="2026-08-31")

        # Both should be identical
        assert result1["history_window"] == result2["history_window"]

    def test_forecast_database_error_returns_degraded(self):
        """Database error should return degraded result, not crash."""
        analytics, mock_db = self._make_analytics()

        # First call is for _get_first_activity_date
        # Second call is for _get_continuous_daily_totals
        mock_db.fetch_one.side_effect = [None, Exception("Database connection failed")]
        mock_db.fetch_all.side_effect = Exception("Database connection failed")

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        # Should return degraded result with all days as missing
        assert result["history_window"]["missing_days"] == 7
        assert result["quality_metrics"]["missing_ratio"] == 1.0

    def test_forecast_new_user_boundary(self):
        """New user with recent first activity should have bounded window."""
        analytics, mock_db = self._make_analytics()

        # First activity on 2026-08-28
        mock_db.fetch_one.return_value = {"first_date": "2026-08-28"}
        daily_data = [
            {"date": "2026-08-28", "tokens": 100, "requests": 10},
            {"date": "2026-08-29", "tokens": 100, "requests": 10},
            {"date": "2026-08-30", "tokens": 100, "requests": 10},
        ]
        mock_db.fetch_all.return_value = daily_data

        result = analytics.get_forecast(days=7, business_date="2026-08-31")

        # Window should start at first activity date
        assert result["history_window"]["start_date"] == "2026-08-28"
        assert result["history_window"]["total_days"] == 3
