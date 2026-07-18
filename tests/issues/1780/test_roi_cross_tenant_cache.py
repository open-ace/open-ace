#!/usr/bin/env python3
"""RED tests: ROI cached queries must be tenant-scoped (PR #1780 finding R2/R3).

The @cached ROI read paths previously queried ``daily_usage`` with only
optional user_id/tool_name filters and a global cache key, so tenant A
materialized an all-tenant aggregate that tenant B read for 60s.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.analytics.roi_calculator import ROICalculator
from app.utils.cache import get_cache


def _seed_daily_usage(db: MagicMock) -> None:
    db.fetch_one.return_value = {
        "request_count": 10,
        "total_input_tokens": 1000,
        "total_output_tokens": 500,
        "total_tokens": 1500,
    }
    db.fetch_all.return_value = [
        {
            "tool_name": "qwen",
            "model": "claude-3-haiku",
            "input_tokens": 1000,
            "output_tokens": 500,
        }
    ]


class TestRoiTenantScoping:
    def setup_method(self):
        get_cache().clear()

    def test_calculate_roi_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.calculate_roi("2026-01-01", "2026-01-31", tenant_id=7)

        executed = calc.db.fetch_one.call_args[0][0]
        params = calc.db.fetch_one.call_args[0][1]
        assert "tenant_id" in executed
        assert 7 in params

    def test_calculate_roi_model_query_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.calculate_roi("2026-01-01", "2026-01-31", tenant_id=7)

        model_query = calc.db.fetch_all.call_args[0][0]
        model_params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in model_query
        assert 7 in model_params

    def test_calculate_roi_cache_key_is_tenant_distinct(self):
        tenant_a_db = MagicMock()
        tenant_b_db = MagicMock()
        _seed_daily_usage(tenant_a_db)
        _seed_daily_usage(tenant_b_db)
        calc_a = ROICalculator(db=tenant_a_db)
        calc_b = ROICalculator(db=tenant_b_db)

        calc_a.calculate_roi("2026-01-01", "2026-01-31", tenant_id=1)
        calc_b.calculate_roi("2026-01-01", "2026-01-31", tenant_id=2)

        # Tenant B must hit its own DB query, not read tenant A's cached result.
        assert tenant_b_db.fetch_one.called

    def test_get_cost_breakdown_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_cost_breakdown("2026-01-01", "2026-01-31", tenant_id=7)

        query = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in query
        assert 7 in params

    def test_get_daily_costs_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_daily_costs("2026-01-01", "2026-01-31", tenant_id=7)

        query = calc.db.fetch_all.call_args[0][0]
        params = calc.db.fetch_all.call_args[0][1]
        assert "tenant_id" in query
        assert 7 in params

    def test_get_roi_trend_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_roi_trend(months=6, tenant_id=7)

        # trend issues two fetch_all calls; both must be tenant-scoped
        for call in calc.db.fetch_all.call_args_list:
            query = call[0][0]
            params = call[0][1]
            assert "tenant_id" in query
            assert 7 in params

    def test_get_roi_by_tool_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_roi_by_tool("2026-01-01", "2026-01-31", tenant_id=7)

        for call in calc.db.fetch_all.call_args_list:
            query = call[0][0]
            params = call[0][1]
            assert "tenant_id" in query
            assert 7 in params

    def test_get_roi_by_user_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        calc.db.fetch_all.return_value = []

        calc.get_roi_by_user("2026-01-01", "2026-01-31", tenant_id=7)

        for call in calc.db.fetch_all.call_args_list:
            query = call[0][0]
            params = call[0][1]
            assert "tenant_id" in query
            assert 7 in params

    def test_get_summary_stats_filters_by_tenant_id(self):
        calc = ROICalculator(db=MagicMock())
        _seed_daily_usage(calc.db)

        calc.get_summary_stats("2026-01-01", "2026-01-31", tenant_id=7)

        for call in calc.db.fetch_one.call_args_list + calc.db.fetch_all.call_args_list:
            query = call[0][0]
            params = call[0][1]
            assert "tenant_id" in query
            assert 7 in params
