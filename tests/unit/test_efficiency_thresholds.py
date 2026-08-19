"""Unit tests for EfficiencyThresholds configuration module."""

import os

import pytest

from app.modules.analytics.efficiency_thresholds import (
    DEFAULT_THRESHOLDS,
    AvgTokensPerRequestThresholds,
    CostPerRequestThresholds,
    EfficiencyThresholds,
    OutputRatioThresholds,
    WasteCalculationThresholds,
    get_thresholds,
)


class TestEfficiencyThresholds:
    """Test EfficiencyThresholds configuration."""

    def test_default_thresholds(self):
        """Test default thresholds are valid."""
        is_valid, errors = DEFAULT_THRESHOLDS.validate()
        assert is_valid, f"Default thresholds should be valid: {errors}"

    def test_base_score_default(self):
        """Test base_score default value."""
        assert DEFAULT_THRESHOLDS.base_score == 60.0

    def test_output_ratio_defaults(self):
        """Test output ratio threshold defaults."""
        assert DEFAULT_THRESHOLDS.output_ratio.ideal_range == (30.0, 50.0)
        assert DEFAULT_THRESHOLDS.output_ratio.good_range == (20.0, 60.0)
        assert DEFAULT_THRESHOLDS.output_ratio.acceptable_min == 10.0

    def test_cost_per_request_defaults(self):
        """Test cost per request threshold defaults."""
        assert DEFAULT_THRESHOLDS.cost_per_request.excellent == 0.01
        assert DEFAULT_THRESHOLDS.cost_per_request.good == 0.05
        assert DEFAULT_THRESHOLDS.cost_per_request.acceptable == 0.10

    def test_avg_tokens_defaults(self):
        """Test avg tokens threshold defaults."""
        assert DEFAULT_THRESHOLDS.avg_tokens_per_request.efficient_range == (500, 2000)
        assert DEFAULT_THRESHOLDS.avg_tokens_per_request.acceptable_range == (200, 5000)

    def test_waste_calculation_defaults(self):
        """Test waste calculation threshold defaults."""
        assert DEFAULT_THRESHOLDS.waste_calculation.output_ratio_threshold == 0.1
        assert DEFAULT_THRESHOLDS.waste_calculation.waste_coefficient == 50.0

    def test_validate_valid_thresholds(self):
        """Test validation with valid thresholds."""
        thresholds = EfficiencyThresholds()
        is_valid, errors = thresholds.validate()
        assert is_valid
        assert len(errors) == 0

    def test_validate_invalid_base_score(self):
        """Test validation catches invalid base_score."""
        thresholds = EfficiencyThresholds()
        thresholds.base_score = 150.0  # Invalid: > 100
        is_valid, errors = thresholds.validate()
        assert not is_valid
        assert any("base_score" in e for e in errors)

    def test_validate_invalid_output_ratio_range(self):
        """Test validation catches invalid output_ratio range."""
        thresholds = EfficiencyThresholds()
        thresholds.output_ratio.ideal_range = (50.0, 30.0)  # Invalid: min > max
        is_valid, errors = thresholds.validate()
        assert not is_valid
        assert any("ideal_range" in e for e in errors)

    def test_validate_ideal_not_within_good(self):
        """Test validation catches ideal_range not within good_range."""
        thresholds = EfficiencyThresholds()
        thresholds.output_ratio.ideal_range = (10.0, 70.0)  # Outside good_range
        thresholds.output_ratio.good_range = (20.0, 60.0)
        is_valid, errors = thresholds.validate()
        assert not is_valid
        assert any("ideal_range" in e for e in errors)

    def test_validate_cost_thresholds_not_ordered(self):
        """Test validation catches unordered cost thresholds."""
        thresholds = EfficiencyThresholds()
        thresholds.cost_per_request.excellent = 0.10
        thresholds.cost_per_request.good = 0.05
        thresholds.cost_per_request.acceptable = 0.01
        is_valid, errors = thresholds.validate()
        assert not is_valid
        assert any("cost_per_request" in e for e in errors)

    def test_validate_waste_threshold_range(self):
        """Test validation catches invalid waste threshold."""
        thresholds = EfficiencyThresholds()
        thresholds.waste_calculation.output_ratio_threshold = 1.5  # Invalid: > 1
        is_valid, errors = thresholds.validate()
        assert not is_valid
        assert any("output_ratio_threshold" in e for e in errors)

    def test_from_env_no_vars(self):
        """Test from_env with no environment variables."""
        # Clear any efficiency env vars
        for key in list(os.environ.keys()):
            if key.startswith("OPENACE_EFFICIENCY_"):
                del os.environ[key]

        thresholds = EfficiencyThresholds.from_env()
        assert thresholds.base_score == DEFAULT_THRESHOLDS.base_score

    def test_from_env_with_base_score(self):
        """Test from_env with OPENACE_EFFICIENCY_BASE_SCORE."""
        os.environ["OPENACE_EFFICIENCY_BASE_SCORE"] = "70.0"
        try:
            thresholds = EfficiencyThresholds.from_env()
            assert thresholds.base_score == 70.0
        finally:
            del os.environ["OPENACE_EFFICIENCY_BASE_SCORE"]

    def test_from_env_invalid_value(self):
        """Test from_env with invalid environment value falls back to default."""
        os.environ["OPENACE_EFFICIENCY_BASE_SCORE"] = "invalid"
        try:
            thresholds = EfficiencyThresholds.from_env()
            # Should fall back to default
            assert thresholds.base_score == DEFAULT_THRESHOLDS.base_score
        finally:
            del os.environ["OPENACE_EFFICIENCY_BASE_SCORE"]

    def test_from_env_negative_value(self):
        """Test from_env with negative value falls back to default."""
        os.environ["OPENACE_EFFICIENCY_BASE_SCORE"] = "-10"
        try:
            thresholds = EfficiencyThresholds.from_env()
            # Should fall back to default (negative not allowed)
            assert thresholds.base_score == DEFAULT_THRESHOLDS.base_score
        finally:
            del os.environ["OPENACE_EFFICIENCY_BASE_SCORE"]

    def test_get_thresholds_default(self):
        """Test get_thresholds returns valid defaults."""
        # Clear env vars
        for key in list(os.environ.keys()):
            if key.startswith("OPENACE_EFFICIENCY_"):
                del os.environ[key]

        thresholds = get_thresholds()
        assert isinstance(thresholds, EfficiencyThresholds)
        assert thresholds.version == "2.0"


class TestOutputRatioThresholds:
    """Test OutputRatioThresholds."""

    def test_default_values(self):
        """Test default values are reasonable."""
        thresholds = OutputRatioThresholds()
        assert thresholds.ideal_range[0] < thresholds.ideal_range[1]
        assert thresholds.good_range[0] < thresholds.good_range[1]
        assert thresholds.ideal_score_bonus > thresholds.good_score_bonus


class TestCostPerRequestThresholds:
    """Test CostPerRequestThresholds."""

    def test_default_values(self):
        """Test default values are reasonable."""
        thresholds = CostPerRequestThresholds()
        assert thresholds.excellent < thresholds.good < thresholds.acceptable


class TestAvgTokensPerRequestThresholds:
    """Test AvgTokensPerRequestThresholds."""

    def test_default_values(self):
        """Test default values are reasonable."""
        thresholds = AvgTokensPerRequestThresholds()
        assert thresholds.efficient_range[0] < thresholds.efficient_range[1]
        assert thresholds.acceptable_range[0] < thresholds.acceptable_range[1]


class TestWasteCalculationThresholds:
    """Test WasteCalculationThresholds."""

    def test_default_values(self):
        """Test default values are reasonable."""
        thresholds = WasteCalculationThresholds()
        assert 0 < thresholds.output_ratio_threshold < 1
        assert 0 < thresholds.waste_coefficient <= 100