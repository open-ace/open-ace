#!/usr/bin/env python3
"""Tests: CostOptimizer cached queries must be tenant-scoped (Issue #1818 R5).

The @cached CostOptimizer methods previously queried ``daily_usage`` with no
tenant_id filter and a global cache key, so tenant A materialized an
all-tenant aggregate that tenant B could read for 60-120s.

These tests verify that tenant_id is now included in both the SQL filter
and the cache key.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.analytics.cost_optimizer import CostOptimizer
from app.utils.cache import get_cache


def _seed_daily_usage(db: MagicMock) -> None:
    db.fetch_all.return_value = [
        {
            "tool_name": "qwen",
            "model": "claude-3-haiku",
            "date": "2026-01-15",
            "input_tokens": 1000,
            "output_tokens": 500,
        }
    ]


class TestCostOptimizerTenantScoping:
    def setup_method(self):
        get_cache().clear()

    def test_analyze_filters_by_tenant_id(self):
        """analyze() must include tenant_id in SQL WHERE clause."""
        calc = CostOptimizer(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.analyze(days=30, tenant_id=7)

        executed = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in executed
        assert 7 in params

    def test_analyze_cache_key_is_tenant_distinct(self):
        """Cache keys for different tenants must not collide."""
        tenant_a_db = MagicMock()
        tenant_b_db = MagicMock()
        _seed_daily_usage(tenant_a_db)
        _seed_daily_usage(tenant_b_db)

        calc_a = CostOptimizer(db=tenant_a_db)
        calc_b = CostOptimizer(db=tenant_b_db)

        calc_a.analyze(days=30, tenant_id=1)
        calc_b.analyze(days=30, tenant_id=2)

        # Tenant B must hit its own DB query, not read tenant A's cached result.
        assert tenant_b_db.fetch_all.called

    def test_get_cost_trend_filters_by_tenant_id(self):
        """get_cost_trend() must include tenant_id in SQL WHERE clause."""
        calc = CostOptimizer(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_cost_trend(days=30, tenant_id=7)

        query = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in query
        assert 7 in params

    def test_get_cost_trend_cache_key_is_tenant_distinct(self):
        """Cache keys for get_cost_trend must be tenant-distinct."""
        tenant_a_db = MagicMock()
        tenant_b_db = MagicMock()
        tenant_a_db.fetch_all.return_value = []
        tenant_b_db.fetch_all.return_value = []

        calc_a = CostOptimizer(db=tenant_a_db)
        calc_b = CostOptimizer(db=tenant_b_db)

        calc_a.get_cost_trend(days=30, tenant_id=1)
        calc_b.get_cost_trend(days=30, tenant_id=2)

        assert tenant_b_db.fetch_all.called

    def test_get_efficiency_report_filters_by_tenant_id(self):
        """get_efficiency_report() must include tenant_id in SQL WHERE clause."""
        calc = CostOptimizer(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.get_efficiency_report(days=30, tenant_id=7)

        # _get_usage_data is called internally; verify the query
        executed = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in executed
        assert 7 in params

    def test_get_efficiency_report_cache_key_is_tenant_distinct(self):
        """Cache keys for get_efficiency_report must be tenant-distinct."""
        tenant_a_db = MagicMock()
        tenant_b_db = MagicMock()
        _seed_daily_usage(tenant_a_db)
        _seed_daily_usage(tenant_b_db)

        calc_a = CostOptimizer(db=tenant_a_db)
        calc_b = CostOptimizer(db=tenant_b_db)

        calc_a.get_efficiency_report(days=30, tenant_id=1)
        calc_b.get_efficiency_report(days=30, tenant_id=2)

        assert tenant_b_db.fetch_all.called

    def test_tenant_id_none_means_no_filter(self):
        """tenant_id=None should NOT add a tenant filter (global query)."""
        calc = CostOptimizer(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.analyze(days=30, tenant_id=None)

        executed = calc.db.fetch_all.call_args[0][0]
        # Should NOT contain tenant_id filter for global scope
        assert "tenant_id" not in executed

    def test_internal_get_usage_data_passes_tenant_id(self):
        """_get_usage_data must pass tenant_id through to the SQL query."""
        calc = CostOptimizer(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc._get_usage_data("2026-01-01", "2026-01-31", tenant_id=42)

        executed = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in executed
        assert 42 in params
