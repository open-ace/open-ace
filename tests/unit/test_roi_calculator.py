"""Unit tests for ROICalculator module."""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.modules.analytics.roi_calculator import (
    CostBreakdown,
    ModelPricing,
    ROIAssumptions,
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

    def test_get_cost_breakdown_aggregates_same_tool(self):
        """Same tool_name with different models_used should be aggregated."""
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen-code",
                "model": json.dumps(["glm-5"]),
                "requests": 50,
                "input_tokens": 10000,
                "output_tokens": 5000,
            },
            {
                "tool_name": "qwen-code-cli",
                "model": json.dumps(["qwen3.6-plus"]),
                "requests": 30,
                "input_tokens": 5000,
                "output_tokens": 2000,
            },
            {
                "tool_name": "claude-code",
                "model": json.dumps(["claude-3-sonnet"]),
                "requests": 10,
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
        ]
        breakdown = calc.get_cost_breakdown("2026-01-01", "2026-01-31")

        # Should aggregate to 2 items: qwen and claude
        assert len(breakdown) == 2

        qwen_item = next(b for b in breakdown if b.tool_name == "qwen")
        assert qwen_item.requests == 80
        assert qwen_item.input_tokens == 15000
        assert qwen_item.output_tokens == 7000

        # Verify model merge
        models = json.loads(qwen_item.model)
        assert "glm-5" in models
        assert "qwen3.6-plus" in models

    def test_get_cost_breakdown_case_drift_collapses(self):
        """Pure case drift (qwen/Qwen/QWEN) — not known aliases — must still
        collapse to a single qwen slice. This is the actual prod failure mode."""
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen",
                "model": json.dumps(["glm-5"]),
                "requests": 10,
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "tool_name": "Qwen",
                "model": json.dumps(["qwen3.5-plus"]),
                "requests": 20,
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
            {
                "tool_name": "QWEN",
                "model": json.dumps(["kimi"]),
                "requests": 5,
                "input_tokens": 500,
                "output_tokens": 250,
            },
        ]
        breakdown = calc.get_cost_breakdown("2026-01-01", "2026-01-31")

        # The three case-drift rows must collapse to ONE qwen slice.
        assert len(breakdown) == 1
        assert breakdown[0].tool_name == "qwen"
        assert breakdown[0].requests == 35
        assert breakdown[0].input_tokens == 3500
        assert breakdown[0].output_tokens == 1750

    def test_get_cost_breakdown_cache_key_includes_start_date(self):
        """R1 verification: skip_args=[0] must skip `self`, NOT start_date.
        Distinct date ranges must produce distinct cache keys — otherwise
        different ranges collide and return stale/incorrect breakdowns."""
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "qwen",
                "model": json.dumps(["glm-5"]),
                "requests": 1,
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ]
        first = calc.get_cost_breakdown("2026-01-01", "2026-01-31")

        # Swap the underlying data and query a DIFFERENT start_date.
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "claude",
                "model": json.dumps(["claude-3-opus"]),
                "requests": 9,
                "input_tokens": 9000,
                "output_tokens": 9000,
            }
        ]
        second = calc.get_cost_breakdown("2026-02-01", "2026-02-28")

        # If start_date were dropped from the key, `second` would return the
        # cached qwen result. Distinct keys => fresh, correct result.
        assert first[0].tool_name == "qwen"
        assert second[0].tool_name == "claude"

    def test_merge_models(self):
        """Test _merge_models helper method."""
        # Normal merge
        result = ROICalculator._merge_models(json.dumps(["glm-5"]), json.dumps(["qwen3.6-plus"]))
        models = json.loads(result)
        assert "glm-5" in models
        assert "qwen3.6-plus" in models
        assert len(models) == 2

        # Deduplication
        result = ROICalculator._merge_models(
            json.dumps(["glm-5", "qwen3.6-plus"]), json.dumps(["glm-5"])
        )
        models = json.loads(result)
        assert len(models) == 2  # Should not duplicate glm-5

        # Empty values
        result = ROICalculator._merge_models("unknown", json.dumps(["glm-5"]))
        models = json.loads(result)
        assert "glm-5" in models

        # Both empty
        result = ROICalculator._merge_models("", "")
        assert result == "unknown"

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

    def test_get_daily_costs_normalizes_date_object(self):
        """PostgreSQL returns `daily_usage.date` as a datetime.date object; the
        output must be normalized to a YYYY-MM-DD string so Flask jsonify does
        not serialize it as an RFC822 HTTP-date onto the chart axis. The plain
        string mock above only covers the SQLite path and would miss this."""
        calc, mock_db = self._make_calculator()
        mock_db.fetch_all.return_value = [
            {"date": date(2026, 6, 1), "input_tokens": 1000, "output_tokens": 500},
            {"date": date(2026, 6, 2), "input_tokens": 2000, "output_tokens": 1000},
            {"date": None, "input_tokens": 300, "output_tokens": 100},
        ]
        costs = calc.get_daily_costs("2026-06-01", "2026-06-30")
        assert len(costs) == 3
        assert costs[0]["date"] == "2026-06-01"
        assert costs[1]["date"] == "2026-06-02"
        # None date is preserved (not coerced to "None" string)
        assert costs[2]["date"] is None
        # No date object should leak through
        assert all(not hasattr(c["date"], "strftime") for c in costs if c["date"])

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

    def test_calculate_roi_includes_assumptions(self):
        calc, mock_db = self._make_calculator()
        mock_db.fetch_one.return_value = {
            "request_count": 12,
            "total_input_tokens": 2000,
            "total_output_tokens": 1000,
            "total_tokens": 3000,
        }
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "test",
                "model": "claude-3-haiku",
                "input_tokens": 2000,
                "output_tokens": 1000,
            }
        ]

        roi = calc.calculate_roi("2026-01-01", "2026-01-31")

        assert roi is not None
        assert roi.assumptions is not None
        assert roi.assumptions.to_dict() == {
            "hourly_labor_cost": 50.0,
            "productivity_multiplier": 10.0,
            "avg_time_saved_per_request": 5.0,
            "currency": "USD",
        }

    def test_custom_assumptions_override_roi_estimate(self):
        mock_db = MagicMock()
        calc = ROICalculator(
            db=mock_db,
            assumptions=ROIAssumptions(
                hourly_labor_cost=120.0,
                productivity_multiplier=4.0,
                avg_time_saved_per_request=15.0,
                currency="CNY",
            ),
        )
        mock_db.fetch_one.return_value = {
            "request_count": 20,
            "total_input_tokens": 4000,
            "total_output_tokens": 1000,
            "total_tokens": 5000,
        }
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "test",
                "model": "claude-3-haiku",
                "input_tokens": 4000,
                "output_tokens": 1000,
            }
        ]

        roi = calc.calculate_roi("2026-01-01", "2026-01-31")

        assert roi is not None
        assert roi.assumptions is not None
        assert roi.assumptions.currency == "CNY"
        assert roi.estimated_hours_saved == 5.0
        assert roi.estimated_savings == 600.0
        assert roi.productivity_gain == 300.0

    def test_summary_stats_exposes_active_assumptions(self):
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

        assert stats["assumptions"] == {
            "hourly_labor_cost": 50.0,
            "productivity_multiplier": 10.0,
            "avg_time_saved_per_request": 5.0,
            "currency": "USD",
        }

    def test_roi_assumptions_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENACE_ROI_HOURLY_LABOR_COST", "88")
        monkeypatch.setenv("OPENACE_ROI_PRODUCTIVITY_MULTIPLIER", "6.5")
        monkeypatch.setenv("OPENACE_ROI_AVG_TIME_SAVED_PER_REQUEST", "12")
        monkeypatch.setenv("OPENACE_ROI_CURRENCY", "cny")

        assumptions = ROIAssumptions.from_env()

        assert assumptions.hourly_labor_cost == 88.0
        assert assumptions.productivity_multiplier == 6.5
        assert assumptions.avg_time_saved_per_request == 12.0
        assert assumptions.currency == "CNY"

    def test_roi_assumptions_invalid_env_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("OPENACE_ROI_HOURLY_LABOR_COST", "abc")
        monkeypatch.setenv("OPENACE_ROI_PRODUCTIVITY_MULTIPLIER", "-2")
        monkeypatch.setenv("OPENACE_ROI_AVG_TIME_SAVED_PER_REQUEST", "0")
        monkeypatch.setenv("OPENACE_ROI_CURRENCY", "")

        assumptions = ROIAssumptions.from_env()

        assert assumptions.to_dict() == {
            "hourly_labor_cost": 50.0,
            "productivity_multiplier": 10.0,
            "avg_time_saved_per_request": 5.0,
            "currency": "USD",
        }


class TestParseModelName:
    """Test parse_model_name method for various model name formats."""

    def _make_calculator(self):
        mock_db = MagicMock()
        calc = ROICalculator(db=mock_db)
        return calc

    def setup_method(self):
        get_cache().clear()

    def test_parse_simple_string(self):
        """Test parsing simple model name string."""
        calc = self._make_calculator()
        result = calc.parse_model_name("claude-3-sonnet")
        assert result == "claude-3-sonnet"

    def test_parse_json_array_single_model(self):
        """Test parsing JSON array string with single model."""
        calc = self._make_calculator()
        result = calc.parse_model_name('["claude-3-sonnet"]')
        assert result == "claude-3-sonnet"

    def test_parse_json_array_multiple_models(self):
        """Test parsing JSON array string with multiple models."""
        calc = self._make_calculator()
        result = calc.parse_model_name('["claude-3-sonnet", "gpt-4"]')
        # Should return first model
        assert result == "claude-3-sonnet"

    def test_parse_json_array_empty(self):
        """Test parsing empty JSON array."""
        calc = self._make_calculator()
        result = calc.parse_model_name("[]")
        assert result == "default"

    def test_parse_json_string_value(self):
        """Test parsing JSON string value (not array)."""
        calc = self._make_calculator()
        result = calc.parse_model_name('"claude-3-haiku"')
        assert result == "claude-3-haiku"

    def test_parse_none_value(self):
        """Test parsing None value."""
        calc = self._make_calculator()
        result = calc.parse_model_name(None)
        assert result == "default"

    def test_parse_non_string_value(self):
        """Test parsing non-string value (e.g., integer)."""
        calc = self._make_calculator()
        result = calc.parse_model_name(123)
        assert result == "123"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON string (fallback to raw value)."""
        calc = self._make_calculator()
        result = calc.parse_model_name("not-a-json-string")
        assert result == "not-a-json-string"

    def test_parse_json_with_special_chars(self):
        """Test parsing JSON with special characters."""
        calc = self._make_calculator()
        result = calc.parse_model_name('["claude-3-5-sonnet@20240620"]')
        assert result == "claude-3-5-sonnet@20240620"

    def test_calculate_cost_with_json_model(self):
        """Test calculate_cost with JSON array model name."""
        calc = self._make_calculator()
        # Using JSON array format for model
        input_cost, output_cost, total = calc.calculate_cost(1000, 500, '["claude-3-opus"]')
        # Should use claude-3-opus pricing
        assert input_cost == 0.015
        assert output_cost == 0.0375


class TestEfficiencyScore:
    """Test efficiency score calculation."""

    def _make_calculator(self):
        mock_db = MagicMock()
        calc = ROICalculator(db=mock_db)
        return calc

    def setup_method(self):
        get_cache().clear()

    def test_efficiency_score_base(self):
        """Test base efficiency score with no tokens."""
        calc = self._make_calculator()
        score = calc._calculate_efficiency_score(0, 0, 0, 0, 0.0, 0.0)
        assert score == 60.0

    def test_efficiency_score_with_good_output_ratio(self):
        """Test efficiency score with ideal output ratio (30-50%)."""
        calc = self._make_calculator()
        # 40% output ratio: 4000 output out of 10000 total
        score = calc._calculate_efficiency_score(10000, 6000, 4000, 100, 1.0, 2.0)
        # Base 60 + 20 (output ratio) + 15 (cost-benefit >= 2) + 5 (avg tokens 100)
        # avg_tokens_per_request = 10000/100 = 100, not in ideal range
        # So: 60 + 20 + 15 = 95
        assert score >= 95

    def test_efficiency_score_capped_at_100(self):
        """Test efficiency score is capped at 100."""
        calc = self._make_calculator()
        # Very high savings ratio and ideal output ratio
        # output_ratio = 4000/10000 = 40% (ideal: 30-50%) -> +20
        # cost_benefit = 100.0/0.01 = 10000 -> +15
        # avg_tokens_per_request = 10000/100 = 100 -> not in ideal range
        score = calc._calculate_efficiency_score(10000, 6000, 4000, 100, 0.01, 100.0)
        # Base 60 + 20 (output ratio) + 15 (cost-benefit) = 95, not capped
        # Need higher to reach 100
        # Let's use ideal avg_tokens_per_request: 500-2000
        score = calc._calculate_efficiency_score(1000, 600, 400, 1, 0.01, 100.0)
        # Base 60 + 20 (output ratio 40%) + 15 (cost-benefit) + 5 (avg_tokens=1000)
        # = 100
        assert score == 100.0

    def test_efficiency_score_with_negative_roi(self):
        """Test efficiency score with negative ROI (low cost-benefit)."""
        calc = self._make_calculator()
        # Low savings, high cost
        # output_ratio = 2000/10000 = 20% -> +15 (20-60 range)
        # cost_benefit = 5.0/10.0 = 0.5 -> +5 (>=0.5)
        # avg_tokens = 10000/100 = 100 -> +0 (not in range)
        score = calc._calculate_efficiency_score(10000, 8000, 2000, 100, 10.0, 5.0)
        # Base 60 + 15 + 5 = 80
        assert score == 80.0

    def test_roi_metrics_has_efficiency_score(self):
        """Test ROIMetrics includes efficiency_score field."""
        metrics = ROIMetrics(
            period="2026-01-01 to 2026-01-31",
            start_date="2026-01-01",
            end_date="2026-01-31",
            total_cost=10.5,
            tokens_used=10000,
            requests_made=100,
            efficiency_score=85.5,
        )
        d = metrics.to_dict()
        assert "efficiency_score" in d
        assert d["efficiency_score"] == 85.5
