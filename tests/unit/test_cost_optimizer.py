"""Unit tests for CostOptimizer module."""

from unittest.mock import MagicMock

import pytest

from app.modules.analytics.cost_optimizer import (
    CostOptimizer,
    OptimizationSuggestion,
    OptimizationType,
    Priority,
)


class TestCostOptimizer:
    """Test CostOptimizer."""

    def _make_optimizer(self):
        mock_db = MagicMock()
        opt = CostOptimizer(db=mock_db)
        return opt, mock_db

    def test_is_expensive_model(self):
        opt, _ = self._make_optimizer()
        assert opt._is_expensive_model("claude-3-opus") is True
        assert opt._is_expensive_model("gpt-4") is True
        assert opt._is_expensive_model("qwen-max") is True
        assert opt._is_expensive_model("claude-3-haiku") is False

    def test_find_cheaper_alternative(self):
        opt, _ = self._make_optimizer()
        assert opt._find_cheaper_alternative("claude-3-opus") == "claude-3-5-sonnet"
        assert opt._find_cheaper_alternative("gpt-4") == "gpt-4-turbo"
        assert opt._find_cheaper_alternative("qwen-max") == "qwen-plus"

    def test_find_cheaper_alternative_cheapest(self):
        opt, _ = self._make_optimizer()
        result = opt._find_cheaper_alternative("gpt-3.5-turbo")
        assert result is None

    def test_find_cheaper_alternative_unknown(self):
        opt, _ = self._make_optimizer()
        result = opt._find_cheaper_alternative("unknown-model")
        assert result is None

    def test_calculate_cost(self):
        opt, _ = self._make_optimizer()
        cost = opt._calculate_cost("claude-3-opus", 1000, 1000)
        assert cost == 0.015 + 0.075

    def test_calculate_model_savings(self):
        opt, _ = self._make_optimizer()
        savings = opt._calculate_model_savings("claude-3-opus", "claude-3-haiku", 10000, 10000)
        assert savings > 0

    def test_savings_percentage(self):
        opt, _ = self._make_optimizer()
        pct = opt._savings_percentage(0.5, 10000, 10000, "claude-3-opus")
        assert pct > 0

    def test_analyze_model_usage_short_requests(self):
        opt, _ = self._make_optimizer()
        data = {
            "by_model": [
                {
                    "tool_name": "test",
                    "model": "claude-3-opus",
                    "requests": 100,
                    "input_tokens": 200000,
                    "output_tokens": 50000,
                    "avg_tokens_per_request": 250,
                }
            ]
        }
        suggestions = opt._analyze_model_usage(data, "2026-01-01", "2026-01-31")
        assert any(s.suggestion_type == OptimizationType.MODEL_SWITCH.value for s in suggestions)

    def test_analyze_model_usage_long_requests(self):
        opt, _ = self._make_optimizer()
        data = {
            "by_model": [
                {
                    "tool_name": "test",
                    "model": "claude-3-opus",
                    "requests": 100,
                    "input_tokens": 500000,
                    "output_tokens": 200000,
                    "avg_tokens_per_request": 7000,
                }
            ]
        }
        suggestions = opt._analyze_model_usage(data, "2026-01-01", "2026-01-31")
        assert len(suggestions) == 0

    def test_analyze_token_efficiency_high_input(self):
        opt, _ = self._make_optimizer()
        data = {
            "overall": {
                "total_input_tokens": 9000,
                "total_output_tokens": 1000,
                "total_requests": 10,
            }
        }
        suggestions = opt._analyze_token_efficiency(data)
        assert len(suggestions) >= 1
        assert any(
            s.suggestion_type == OptimizationType.TOKEN_OPTIMIZATION.value for s in suggestions
        )

    def test_analyze_token_efficiency_balanced(self):
        opt, _ = self._make_optimizer()
        data = {
            "overall": {
                "total_input_tokens": 5000,
                "total_output_tokens": 5000,
                "total_requests": 10,
            }
        }
        suggestions = opt._analyze_token_efficiency(data)
        assert len(suggestions) == 0

    def test_get_cost_trend(self):
        opt, mock_db = self._make_optimizer()
        mock_db.fetch_all.return_value = [
            {"date": "2026-01-01", "input_tokens": 1000, "output_tokens": 500},
        ]
        trend = opt.get_cost_trend(days=30)
        assert len(trend) == 1
        assert trend[0]["cost"] > 0

    def test_get_efficiency_report(self):
        opt, mock_db = self._make_optimizer()
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "test",
                "model": "gpt-4",
                "date": "2026-01-01",
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ]
        report = opt.get_efficiency_report(days=30)
        assert "total_tokens" in report
        assert "output_ratio" in report
        assert "unique_tools" in report

    def test_optimization_suggestion_to_dict(self):
        s = OptimizationSuggestion(
            suggestion_id="test_1",
            suggestion_type="model_switch",
            title="Test",
            description="Test suggestion",
            potential_savings=10.0,
            priority="high",
        )
        d = s.to_dict()
        assert d["suggestion_id"] == "test_1"
        assert d["potential_savings"] == 10.0

    def test_analyze_tool_usage_multiple_tools(self):
        opt, _ = self._make_optimizer()
        data = {
            "by_model": [{"tool_name": "tool1"}, {"tool_name": "tool2"}, {"tool_name": "tool3"}]
        }
        suggestions = opt._analyze_tool_usage(data)
        assert len(suggestions) >= 1
        assert suggestions[0].suggestion_type == OptimizationType.TOOL_CONSOLIDATION.value

    def test_analyze_tool_usage_single_tool(self):
        opt, _ = self._make_optimizer()
        data = {"by_model": [{"tool_name": "tool1"}]}
        suggestions = opt._analyze_tool_usage(data)
        assert len(suggestions) == 0
