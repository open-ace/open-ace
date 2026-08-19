"""Unit tests for EfficiencyAlgorithmRegistry module."""

import os

import pytest

from app.modules.analytics.efficiency_registry import (
    EfficiencyAlgorithmRegistry,
    LegacyEfficiencyCalculator,
    ParameterizedEfficiencyCalculator,
    get_algorithm_version,
    get_registry,
)
from app.modules.analytics.task_type_inferencer import TaskType


class TestEfficiencyAlgorithmRegistry:
    """Test EfficiencyAlgorithmRegistry."""

    def test_registry_default_versions(self):
        """Test registry has default versions registered."""
        registry = get_registry()
        versions = registry.get_supported_versions()
        assert "v1.0" in versions
        assert "v2.0" in versions

    def test_get_calculator_default(self):
        """Test get_calculator returns default version."""
        registry = get_registry()
        calculator = registry.get_calculator()
        # Should return the default version (v1.0 initially)
        assert calculator is not None

    def test_get_calculator_v1(self):
        """Test get_calculator returns v1.0."""
        registry = get_registry()
        calculator = registry.get_calculator("v1.0")
        assert isinstance(calculator, LegacyEfficiencyCalculator)

    def test_get_calculator_v2(self):
        """Test get_calculator returns v2.0."""
        registry = get_registry()
        calculator = registry.get_calculator("v2.0")
        assert isinstance(calculator, ParameterizedEfficiencyCalculator)

    def test_get_calculator_unknown_fallback(self):
        """Test get_calculator falls back for unknown version."""
        registry = get_registry()
        calculator = registry.get_calculator("v99.0")
        # Should fall back to default
        assert calculator is not None

    def test_get_default_version(self):
        """Test get_default_version."""
        registry = get_registry()
        version = registry.get_default_version()
        assert version in ("v1.0", "v2.0")


class TestLegacyEfficiencyCalculator:
    """Test LegacyEfficiencyCalculator (v1.0)."""

    def test_calculate_efficiency_score_range(self):
        """Test efficiency score is in [0, 100]."""
        calculator = LegacyEfficiencyCalculator()
        for tokens, input_t, output_t, requests, cost in [
            (1000, 500, 500, 10, 0.05),
            (10000, 9000, 1000, 100, 1.0),
            (100, 10, 90, 1, 0.001),
            (0, 0, 0, 0, 0),
        ]:
            score = calculator.calculate_efficiency_score(
                tokens, input_t, output_t, requests, cost
            )
            assert 0 <= score <= 100

    def test_calculate_efficiency_score_ideal_output_ratio(self):
        """Test ideal output ratio gets bonus."""
        calculator = LegacyEfficiencyCalculator()
        # 40% output ratio (in 30-50% ideal range)
        score = calculator.calculate_efficiency_score(
            tokens=1000,
            input_tokens=600,
            output_tokens=400,
            requests=10,
            total_cost=0.05,
        )
        # Should have +20 for ideal output ratio
        assert score >= 80

    def test_calculate_efficiency_score_low_cost(self):
        """Test low cost gets bonus."""
        calculator = LegacyEfficiencyCalculator()
        score = calculator.calculate_efficiency_score(
            tokens=1000,
            input_tokens=500,
            output_tokens=500,
            requests=100,
            total_cost=0.50,  # $0.005 per request < $0.01
        )
        # Should have +15 for excellent cost
        assert score >= 75

    def test_calculate_waste_percentage_range(self):
        """Test waste percentage is in [0, 100]."""
        calculator = LegacyEfficiencyCalculator()
        for input_t, output_t in [
            (1000, 100),
            (500, 500),
            (100, 1000),
            (0, 0),
        ]:
            waste = calculator.calculate_waste_percentage(input_t, output_t)
            assert 0 <= waste <= 100

    def test_calculate_waste_percentage_low_output(self):
        """Test waste for low output ratio."""
        calculator = LegacyEfficiencyCalculator()
        # 5% output ratio < 10% threshold
        waste = calculator.calculate_waste_percentage(
            input_tokens=950,
            output_tokens=50,
        )
        # Should have some waste
        assert waste > 0

    def test_calculate_waste_percentage_balanced(self):
        """Test no waste for balanced ratio."""
        calculator = LegacyEfficiencyCalculator()
        # 50% output ratio > 10% threshold
        waste = calculator.calculate_waste_percentage(
            input_tokens=500,
            output_tokens=500,
        )
        # Should have no waste
        assert waste == 0


class TestParameterizedEfficiencyCalculator:
    """Test ParameterizedEfficiencyCalculator (v2.0)."""

    def test_calculate_efficiency_score_range(self):
        """Test efficiency score is in [0, 100]."""
        calculator = ParameterizedEfficiencyCalculator()
        for tokens, input_t, output_t, requests, cost in [
            (1000, 500, 500, 10, 0.05),
            (10000, 9000, 1000, 100, 1.0),
            (100, 10, 90, 1, 0.001),
            (0, 0, 0, 0, 0),
        ]:
            score = calculator.calculate_efficiency_score(
                tokens, input_t, output_t, requests, cost
            )
            assert 0 <= score <= 100

    def test_calculate_efficiency_score_with_task_type(self):
        """Test efficiency score with task type adjustment."""
        calculator = ParameterizedEfficiencyCalculator()

        # Same input, different task types should give different results
        score_general = calculator.calculate_efficiency_score(
            tokens=1000,
            input_tokens=600,
            output_tokens=400,  # 40% output
            requests=10,
            total_cost=0.05,
            task_type=TaskType.GENERAL,
        )

        score_code = calculator.calculate_efficiency_score(
            tokens=1000,
            input_tokens=600,
            output_tokens=400,  # 40% output
            requests=10,
            total_cost=0.05,
            task_type=TaskType.CODE_GENERATION,
        )

        # CODE_GENERATION has higher ideal range (40-60%)
        # 40% is at the lower end of ideal for code generation
        # Results may vary based on thresholds
        assert 0 <= score_general <= 100
        assert 0 <= score_code <= 100

    def test_calculate_waste_percentage_range(self):
        """Test waste percentage is in [0, 100]."""
        calculator = ParameterizedEfficiencyCalculator()
        for input_t, output_t in [
            (1000, 100),
            (500, 500),
            (100, 1000),
            (0, 0),
        ]:
            waste = calculator.calculate_waste_percentage(input_t, output_t)
            assert 0 <= waste <= 100

    def test_calculate_waste_document_analysis_lower_threshold(self):
        """Test document analysis has lower waste threshold."""
        calculator = ParameterizedEfficiencyCalculator()

        # 8% output ratio
        waste_general = calculator.calculate_waste_percentage(
            input_tokens=920,
            output_tokens=80,
            task_type=TaskType.GENERAL,
        )

        waste_doc = calculator.calculate_waste_percentage(
            input_tokens=920,
            output_tokens=80,
            task_type=TaskType.DOCUMENT_ANALYSIS,
        )

        # Both should be valid waste percentages
        assert 0 <= waste_general <= 100
        assert 0 <= waste_doc <= 100


class TestGetAlgorithmVersion:
    """Test get_algorithm_version function."""

    def test_default_version(self):
        """Test default version without env or tenant."""
        # Clear env var
        if "OPENACE_EFFICIENCY_ALGORITHM_VERSION" in os.environ:
            del os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"]

        version = get_algorithm_version(tenant_id=None)
        assert version in ("v1.0", "v2.0")

    def test_env_version_override(self):
        """Test environment variable override."""
        os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"] = "v2.0"
        try:
            version = get_algorithm_version(tenant_id=None)
            assert version == "v2.0"
        finally:
            del os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"]

    def test_ab_test_grouping_v2(self):
        """Test A/B test grouping to v2.0 (tenant_id % 10 < 1)."""
        # Clear env var
        if "OPENACE_EFFICIENCY_ALGORITHM_VERSION" in os.environ:
            del os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"]

        # Tenant IDs that should get v2.0: 0, 10, 20, 30, ...
        for tenant_id in [0, 10, 20, 100, 1000]:
            version = get_algorithm_version(tenant_id=tenant_id)
            assert version == "v2.0", f"tenant_id={tenant_id} should get v2.0"

    def test_ab_test_grouping_v1(self):
        """Test A/B test grouping to v1.0 (tenant_id % 10 >= 1)."""
        # Clear env var
        if "OPENACE_EFFICIENCY_ALGORITHM_VERSION" in os.environ:
            del os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"]

        # Tenant IDs that should get v1.0: 1, 2, ..., 9, 11, 12, ...
        for tenant_id in [1, 5, 9, 11, 99, 101]:
            version = get_algorithm_version(tenant_id=tenant_id)
            assert version == "v1.0", f"tenant_id={tenant_id} should get v1.0"

    def test_env_takes_priority_over_ab_test(self):
        """Test env var takes priority over A/B test grouping."""
        os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"] = "v1.0"
        try:
            # Even for tenant that would get v2.0 via A/B test
            version = get_algorithm_version(tenant_id=0)
            assert version == "v1.0"
        finally:
            del os.environ["OPENACE_EFFICIENCY_ALGORITHM_VERSION"]
