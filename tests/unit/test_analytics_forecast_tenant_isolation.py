"""
Unit tests for analytics forecast tenant isolation (Issue #3245).

Tests that the forecast and related analytics methods properly isolate
tenant data, preventing cross-tenant data leakage.

Migrated from Issue #3245.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.analytics.usage_analytics import UsageAnalytics

pytestmark = [pytest.mark.regression, pytest.mark.issue(3245), pytest.mark.security]


class TestForecastTenantIsolation:
    """Test get_forecast() tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_tenant_admin_filters_own_tenant(self):
        """Tenant admin should only see data for own tenant."""
        tenant_id = 123

        # Mock fetch_all to return tenant-specific data
        self.db.fetch_all.return_value = [
            {"date": "2026-09-01", "tokens": 100, "requests": 10},
            {"date": "2026-09-02", "tokens": 200, "requests": 20},
        ]

        # Call with tenant filter
        self.analytics.get_forecast(days=7, tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params

    def test_platform_admin_sees_global_data(self):
        """Platform admin (tenant_id=None) should see global data."""
        # Mock fetch_all to return global data
        self.db.fetch_all.return_value = [
            {"date": "2026-09-01", "tokens": 1000, "requests": 100},
            {"date": "2026-09-02", "tokens": 2000, "requests": 200},
        ]

        # Call without tenant filter (global access)
        self.analytics.get_forecast(days=7, tenant_id=None)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]

        # Verify tenant_id is NOT in query conditions
        assert "tenant_id = ?" not in query

    def test_different_tenants_different_cache_keys(self):
        """Different tenants with same days should have different cache keys."""
        from app.utils.cache import get_cache

        # Clear cache
        get_cache().clear()

        # Mock fetch_all
        self.db.fetch_all.return_value = [
            {"date": "2026-09-01", "tokens": 100, "requests": 10},
        ]

        # Tenant 123
        self.analytics.get_forecast(days=7, tenant_id=123)
        cache_keys_123 = list(get_cache()._backend._cache.keys())

        # Clear cache
        get_cache().clear()

        # Tenant 456
        self.analytics.get_forecast(days=7, tenant_id=456)
        cache_keys_456 = list(get_cache()._backend._cache.keys())

        # Cache keys should be different
        assert cache_keys_123 != cache_keys_456
        assert any(":123" in str(k) or "123" in str(k) for k in cache_keys_123)
        assert any(":456" in str(k) or "456" in str(k) for k in cache_keys_456)


class TestEfficiencyMetricsTenantIsolation:
    """Test get_efficiency_metrics() tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_efficiency_metrics_with_tenant_filter(self):
        """Efficiency metrics should filter by tenant_id."""
        tenant_id = 100

        # Mock fetch_all result
        self.db.fetch_all.return_value = [
            {
                "date": "2026-09-01",
                "tool_name": "tool-a",
                "host_name": "host-a",
                "tokens": 100,
                "input_tokens": 50,
                "output_tokens": 50,
                "requests": 10,
            }
        ]

        # Call with tenant filter
        self.analytics.get_efficiency_metrics("2026-09-01", "2026-09-02", tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params


class TestReportTenantIsolation:
    """Test generate_report() tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_report_with_tenant_filter(self):
        """Report should filter by tenant_id."""
        tenant_id = 200

        # Mock fetch_all results
        self.db.fetch_all.return_value = [
            {
                "date": "2026-09-01",
                "tool_name": "tool-a",
                "host_name": "host-a",
                "tokens": 100,
                "input_tokens": 50,
                "output_tokens": 50,
                "requests": 10,
                "days_active": 1,
            }
        ]

        # Call with tenant filter
        self.analytics.generate_report("2026-09-01", "2026-09-02", tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params


class TestTrendAnalysisTenantIsolation:
    """Test _analyze_trends() tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_trend_analysis_with_tenant_filter(self):
        """Trend analysis should filter by tenant_id."""
        tenant_id = 300

        # Mock fetch_all results for daily totals
        self.db.fetch_all.return_value = [
            {"date": "2026-09-01", "tokens": 100, "requests": 10},
            {"date": "2026-09-02", "tokens": 200, "requests": 20},
        ]

        # Call with tenant filter
        self.analytics._analyze_trends("2026-09-01", "2026-09-02", tenant_id=tenant_id)

        # Verify query was called (called twice: current and previous period)
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params


class TestAnomalyDetectionTenantIsolation:
    """Test _detect_anomalies() tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_anomaly_detection_with_tenant_filter(self):
        """Anomaly detection should filter by tenant_id."""
        tenant_id = 400

        # Mock fetch_all results (need 8+ days for anomaly detection)
        self.db.fetch_all.return_value = [
            {"date": f"2026-09-{i:02d}", "tokens": 100 + i * 10, "requests": 10 + i}
            for i in range(1, 10)
        ]

        # Call with tenant filter
        self.analytics._detect_anomalies("2026-09-01", "2026-09-10", tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params


class TestBreakdownTenantIsolation:
    """Test breakdown methods tenant isolation."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.analytics = UsageAnalytics(db=self.db)

    def test_tool_breakdown_with_tenant_filter(self):
        """Tool breakdown should filter by tenant_id."""
        tenant_id = 500

        # Mock fetch_all result
        self.db.fetch_all.return_value = [
            {
                "tool_name": "tool-a",
                "tokens": 100,
                "input_tokens": 50,
                "output_tokens": 50,
                "requests": 10,
                "days_active": 5,
            }
        ]

        # Call with tenant filter
        self.analytics._get_tool_breakdown("2026-09-01", "2026-09-02", tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params

    def test_host_breakdown_with_tenant_filter(self):
        """Host breakdown should filter by tenant_id."""
        tenant_id = 600

        # Mock fetch_all result
        self.db.fetch_all.return_value = [
            {
                "host_name": "host-a",
                "tokens": 100,
                "requests": 10,
                "days_active": 5,
            }
        ]

        # Call with tenant filter
        self.analytics._get_host_breakdown("2026-09-01", "2026-09-02", tenant_id=tenant_id)

        # Verify query was called
        assert self.db.fetch_all.called
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Verify tenant_id is in query conditions
        assert "tenant_id = ?" in query
        assert tenant_id in params