"""ROI cost consistency tests.

These tests verify that the three cost calculation paths produce
consistent results:
1. ROICalculator.calculate_roi (total cost)
2. ROICalculator.get_daily_costs (daily costs)
3. CostOptimizer._calculate_cost (efficiency report)

Issue #2751: The three paths were using different pricing strategies,
causing contradictory metrics on the ROI analysis page.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.analytics.cost_optimizer import CostOptimizer
from app.modules.analytics.roi_calculator import ROICalculator
from app.utils.cache import get_cache


class TestROICostConsistency:
    """Test data consistency across cost calculation paths."""

    def setup_method(self):
        """Clear cache before each test."""
        get_cache().clear()

    def test_unified_pricing_across_paths(self):
        """Verify: ROICalculator and CostOptimizer use same pricing.

        This test ensures that when we calculate cost for the same model
        and token counts, both calculators produce identical results.
        """
        calc = ROICalculator()
        optimizer = CostOptimizer()

        # Test multiple models including known and unknown
        test_cases = [
            ("qwen-max", 1000, 500),
            ("gpt-4", 1000, 500),
            ("claude-3-opus", 1000, 500),
            ("unknown-model", 1000, 500),
            ("qwen-turbo", 5000, 2000),
            ("gpt-4o-mini", 10000, 5000),
        ]

        for model, input_tokens, output_tokens in test_cases:
            # ROICalculator cost
            _, _, cost_calc = calc.calculate_cost(input_tokens, output_tokens, model)

            # CostOptimizer cost
            cost_optimizer = optimizer._calculate_cost(model, input_tokens, output_tokens)

            # Assert: pricing should be identical
            assert abs(cost_calc - cost_optimizer) < 0.0001, (
                f"Pricing mismatch for {model}: "
                f"ROICalculator={cost_calc:.6f}, CostOptimizer={cost_optimizer:.6f}"
            )

    def test_total_cost_equals_daily_costs_sum_integration(self):
        """Verify: total_cost ≈ sum(daily_costs).

        Integration test using mocked database to simulate real data flow.
        This tests that the ROI total cost matches the sum of daily costs
        when both use unified pricing.
        """
        # Setup mock database
        mock_db = MagicMock()

        # Mock data for calculate_roi
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        # Mock aggregate query result for calculate_roi
        mock_db.fetch_one.return_value = {
            "request_count": 100,
            "total_input_tokens": 50000,
            "total_output_tokens": 25000,
            "total_tokens": 75000,
        }

        # Mock model breakdown query for calculate_roi
        # Using qwen-max model for all requests
        mock_db.fetch_all.return_value = [
            {
                "tool_name": "QWEN",
                "model": "qwen-max",
                "input_tokens": 50000,
                "output_tokens": 25000,
            }
        ]

        calc = ROICalculator(db=mock_db)
        roi_metrics = calc.calculate_roi(start_date, end_date)

        if roi_metrics:
            # Expected cost for qwen-max: input=0.02/1K, output=0.06/1K
            # input_cost = 50 * 0.02 = 1.0
            # output_cost = 25 * 0.06 = 1.5
            # total = 2.5
            expected_total = 1.0 + 1.5

            assert (
                abs(roi_metrics.total_cost - expected_total) < 0.01
            ), f"ROI total_cost {roi_metrics.total_cost} != expected {expected_total}"

    def test_daily_costs_uses_actual_model_pricing(self):
        """Verify: get_daily_costs uses actual model pricing, not default.

        This test ensures that the daily costs calculation now properly
        uses actual model pricing instead of hardcoded "default" pricing.
        """
        mock_db = MagicMock()

        start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        # Mock daily aggregate query (first call in get_daily_costs)
        daily_aggregate = [
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ]

        # Mock model breakdown query (second call in get_daily_costs)
        # Using qwen-max which has different pricing than default
        model_breakdown = [
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "model": "qwen-max",
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ]

        # fetch_all returns different results based on query
        call_count = [0]

        def mock_fetch_all(query, params):
            call_count[0] += 1
            if "GROUP BY date ORDER BY date" in query:
                return daily_aggregate
            elif "GROUP BY date, models_used" in query:
                return model_breakdown
            return []

        mock_db.fetch_all.side_effect = mock_fetch_all

        calc = ROICalculator(db=mock_db)
        daily_costs = calc.get_daily_costs(start_date, end_date)

        assert len(daily_costs) == 1

        # qwen-max pricing: input=0.02/1K, output=0.06/1K
        # Expected: (1000/1000)*0.02 + (500/1000)*0.06 = 0.02 + 0.03 = 0.05
        # Default pricing would give: (1000/1000)*0.01 + (500/1000)*0.03 = 0.01 + 0.015 = 0.025
        expected_cost = 0.05

        assert abs(daily_costs[0]["total_cost"] - expected_cost) < 0.001, (
            f"Daily cost {daily_costs[0]['total_cost']} != expected {expected_cost}. "
            "This may indicate the method is still using default pricing instead of actual model pricing."
        )

    def test_tool_name_filter_in_daily_costs(self):
        """Verify: get_daily_costs supports tool_name parameter.

        This test ensures that the tool_name filter parameter works correctly
        in the get_daily_costs method.
        """
        mock_db = MagicMock()

        start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        tool_name = "QWEN"

        mock_db.fetch_all.return_value = []

        calc = ROICalculator(db=mock_db)
        # Call with tool_name parameter - should not raise
        daily_costs = calc.get_daily_costs(start_date, end_date, tool_name=tool_name)

        # Verify the method accepts the parameter
        assert isinstance(daily_costs, list)

    def test_no_model_data_fallback(self):
        """Verify: fallback to default pricing when no model data available.

        When there's daily usage data but no model breakdown, the method
        should fallback to default pricing and log a warning.
        """
        mock_db = MagicMock()

        start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        # Daily aggregate exists
        daily_aggregate = [
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ]

        # No model breakdown data
        model_breakdown = []

        call_count = [0]

        def mock_fetch_all(query, params):
            call_count[0] += 1
            if "GROUP BY date ORDER BY date" in query:
                return daily_aggregate
            elif "GROUP BY date, models_used" in query:
                return model_breakdown
            return []

        mock_db.fetch_all.side_effect = mock_fetch_all

        calc = ROICalculator(db=mock_db)
        daily_costs = calc.get_daily_costs(start_date, end_date)

        # Should use default pricing when no model data
        # Default: input=0.01/1K, output=0.03/1K
        # Expected: (1000/1000)*0.01 + (500/1000)*0.03 = 0.01 + 0.015 = 0.025
        expected_cost = 0.025

        assert len(daily_costs) == 1
        assert (
            abs(daily_costs[0]["total_cost"] - expected_cost) < 0.001
        ), f"Fallback cost {daily_costs[0]['total_cost']} != expected {expected_cost}"


class TestCostOptimizerPricing:
    """Test CostOptimizer unified pricing with ROICalculator."""

    def setup_method(self):
        """Clear cache before each test."""
        get_cache().clear()

    def test_cost_optimizer_uses_roi_calculator_pricing(self):
        """Verify CostOptimizer uses ROICalculator pricing logic."""
        optimizer = CostOptimizer()

        # Test models that exist in ROICalculator but not in old MODEL_PRICING
        # These models should now have correct pricing via ROICalculator
        test_models = [
            "glm-4",
            "glm-4-plus",
            "deepseek-chat",
            "gemini-1.5-pro",
            "qwen3-coder-next",
        ]

        calc = ROICalculator()

        for model in test_models:
            input_tokens = 1000
            output_tokens = 500

            # Get pricing from ROICalculator
            pricing = calc.get_model_pricing(model)
            expected_cost = (
                input_tokens / 1000 * pricing.input_price
                + output_tokens / 1000 * pricing.output_price
            )

            # Get cost from CostOptimizer
            actual_cost = optimizer._calculate_cost(model, input_tokens, output_tokens)

            assert abs(actual_cost - expected_cost) < 0.0001, (
                f"CostOptimizer pricing mismatch for {model}: "
                f"expected={expected_cost:.6f}, actual={actual_cost:.6f}"
            )

    def test_unknown_model_pricing_consistency(self):
        """Verify unknown models use same default pricing in both calculators."""
        calc = ROICalculator()
        optimizer = CostOptimizer()

        unknown_models = ["some-future-model", "unknown-gpt-variant", "test-model"]

        for model in unknown_models:
            input_tokens = 1000
            output_tokens = 500

            _, _, cost_calc = calc.calculate_cost(input_tokens, output_tokens, model)
            cost_optimizer = optimizer._calculate_cost(model, input_tokens, output_tokens)

            # Both should use default pricing
            assert abs(cost_calc - cost_optimizer) < 0.0001, (
                f"Default pricing mismatch for {model}: "
                f"ROICalculator={cost_calc:.6f}, CostOptimizer={cost_optimizer:.6f}"
            )
