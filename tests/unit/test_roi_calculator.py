"""Unit tests for ROICalculator module."""

from unittest.mock import MagicMock

import pytest

from app.modules.analytics.roi_calculator import (
    CostBreakdown,
    ModelPricing,
    ROICalculator,
    ROIMetrics,
)
from app.utils.cache import get_cache


class TestROICalculator:
    """Test ROI Calculator."""

    def _make_calculator(self):
        mock_db = MagicMock()
        calc = ROICalculator(db=mock_db)
        return calc, mock_db

    def setup_method(self):
        get_cache().clear()

    def test_get_model_pricing_known(self):
        calc, _ = self._make_calculator()
        pricing = calc.get_model_pricing("claude-3-opus")
        assert pricing.input_price == 0.015
        assert pricing.output_price == 0.075

    def test_get_model_pricing_case_insensitive(self):
        calc, _ = self._make_calculator()
        pricing = calc.get_model_pricing("Claude-3-Opus-20240229")
        assert pricing.input_price == 0.015

    def test_get_model_pricing_unknown(self):
        calc, _ = self._make_calculator()
        pricing = calc.get_model_pricing("unknown-model")
        assert pricing.input_price == 0.01
        assert pricing.output_price == 0.03

    def test_calculate_cost(self):
        calc, _ = self._make_calculator()
        input_cost, output_cost, total = calc.calculate_cost(1000, 500, "claude-3-opus")
        assert input_cost == 0.015
        assert output_cost == 0.0375
        assert abs(total - 0.0525) < 0.0001

    def test_calculate_cost_unknown_model(self):
        calc, _ = self._make_calculator()
        _, _, total = calc.calculate_cost(1000, 1000, "unknown")
        assert total == 0.04

    def test_calculate_cost_zero_tokens(self):
        calc, _ = self._make_calculator()
        _, _, total = calc.calculate_cost(0, 0, "claude-3-opus")
        assert total == 0.0

    def test_calculate_roi(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_one.return_value = {
            "request_count": 100,
            "total_input_tokens": 50000,
            "total_output_tokens": 10000,
            "total_tokens": 60000,
        }
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "test",
                "model": "claude-3-opus",
                "input_tokens": 50000,
                "output_tokens": 10000,
            }
        ]
        roi = calc.calculate_roi("2026-01-01", "2026-01-31")
        assert roi is not None
        assert roi.requests_made == 100
        assert roi.total_cost > 0
        assert roi.estimated_hours_saved > 0
        assert roi.estimated_savings > 0
        assert roi.roi_percentage > 0

    def test_calculate_roi_no_data(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_one.return_value = {
            "request_count": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
        }
        mock_db.fetch_all.return_value = []
        roi = calc.calculate_roi("2026-01-01", "2026-01-31")
        # fetch_one returns a row (not None), so ROI is returned with zero data
        assert roi is not None
        assert roi.total_cost == 0

    def test_calculate_roi_zero_requests(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_one.return_value = {
            "request_count": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
        }
        mock_db.fetch_all.return_value = []
        roi = calc.calculate_roi("2026-01-01", "2026-01-31")
        assert roi is not None
        assert roi.cost_per_request == 0

    def test_get_cost_breakdown(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "model": "qwen-max",
                "requests": 50,
                "input_tokens": 10000,
                "output_tokens": 5000,
            }
        ]
        breakdown = calc.get_cost_breakdown("2026-01-01", "2026-01-31")
        assert len(breakdown) == 1
        assert breakdown[0].total_cost > 0
        assert isinstance(breakdown[0], CostBreakdown)

    def test_get_daily_costs(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {"date": "2026-01-01", "input_tokens": 1000, "output_tokens": 500},
            {"date": "2026-01-02", "input_tokens": 2000, "output_tokens": 1000},
        ]
        costs = calc.get_daily_costs("2026-01-01", "2026-01-31")
        assert len(costs) == 2
        assert costs[0]["date"] == "2026-01-01"
        assert costs[0]["total_cost"] > 0

    def test_get_summary_stats(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_one.return_value = {
            "request_count": 50,
            "total_input_tokens": 10000,
            "total_output_tokens": 5000,
            "total_tokens": 15000,
        }
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "test",
                "model": "claude-3-haiku",
                "requests": 50,
                "input_tokens": 10000,
                "output_tokens": 5000,
            }
        ]
        stats = calc.get_summary_stats("2026-01-01", "2026-01-31")
        assert "roi" in stats
        assert "total_cost" in stats
        assert "top_tools" in stats

    def test_roi_metrics_to_dict(self):
        metrics = ROIMetrics(
            period="2026-01-01 to 2026-01-31",
            start_date="2026-01-01",
            end_date="2026-01-31",
            total_cost=10.5,
            tokens_used=10000,
            requests_made=100,
        )
        d = metrics.to_dict()
        assert d["total_cost"] == 10.5
        assert d["tokens_used"] == 10000
        assert "roi_percentage" in d

    def test_cost_breakdown_to_dict(self):
        cb = CostBreakdown(
            tool_name="test",
            model="gpt-4",
            requests=10,
            input_tokens=1000,
            output_tokens=500,
            input_cost=0.03,
            output_cost=0.03,
            total_cost=0.06,
        )
        d = cb.to_dict()
        assert d["tool_name"] == "test"
        assert d["total_cost"] == 0.06

    def test_productivity_multiplier_is_10x(self):
        calc, _ = self._make_calculator()
        assert calc.PRODUCTIVITY_MULTIPLIER == 10.0

    def test_labor_cost_assumptions(self):
        calc, _ = self._make_calculator()
        assert calc.HOURLY_LABOR_COST == 50.0
        assert calc.AVG_TIME_SAVED_PER_REQUEST == 5.0
