"""
Efficiency Algorithm Registry for Version Management.

This module provides a registry for managing multiple efficiency algorithm versions,
enabling A/B testing, gradual rollout, and quick rollback.

Version Lifecycle:
    1. New version registered → 2. Gradual rollout via A/B test →
    3. Full release → 4. Old version deprecated → 5. Old version removed (6+ months)
"""

import logging
from typing import Any, Protocol

from app.modules.analytics.efficiency_thresholds import EfficiencyThresholds, get_thresholds
from app.modules.analytics.task_type_inferencer import TaskType

logger = logging.getLogger(__name__)


class EfficiencyCalculator(Protocol):
    """Protocol for efficiency calculators."""

    def calculate_efficiency_score(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
        task_type: TaskType,
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """Calculate efficiency score (0-100)."""
        ...

    def calculate_waste_percentage(
        self,
        input_tokens: int,
        output_tokens: int,
        task_type: TaskType,
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """Calculate waste percentage (0-100)."""
        ...


class LegacyEfficiencyCalculator:
    """
    Legacy efficiency calculator (v1.0).

    This is the original algorithm from CostOptimizer, preserved as read-only.
    Do NOT modify this class - it represents the baseline for A/B testing.
    """

    version = "v1.0"

    def calculate_efficiency_score(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
        task_type: TaskType = TaskType.GENERAL,  # Ignored in v1.0
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """
        Calculate efficiency score using original algorithm.

        This method preserves the original logic from CostOptimizer for A/B testing.
        """
        # Base score: 60 points
        efficiency_score = 60.0

        # Factor 1: Output ratio (output_tokens / total_tokens)
        if tokens > 0:
            output_ratio = (output_tokens / tokens) * 100
            if 30 <= output_ratio <= 50:
                efficiency_score += 20
            elif 20 <= output_ratio <= 60:
                efficiency_score += 15
            elif output_ratio > 10:
                efficiency_score += 10

        # Factor 2: Cost efficiency (cost_per_request)
        if requests > 0:
            avg_cost = total_cost / requests
            if avg_cost < 0.01:  # Low cost
                efficiency_score += 15
            elif avg_cost < 0.05:
                efficiency_score += 10
            elif avg_cost < 0.10:
                efficiency_score += 5

        # Factor 3: Request efficiency (avg_tokens_per_request)
        if requests > 0:
            avg_tokens = tokens / requests
            if 500 <= avg_tokens <= 2000:
                efficiency_score += 5
            elif 200 <= avg_tokens <= 5000:
                efficiency_score += 3

        return min(efficiency_score, 100.0)

    def calculate_waste_percentage(
        self,
        input_tokens: int,
        output_tokens: int,
        task_type: TaskType = TaskType.GENERAL,  # Ignored in v1.0
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """
        Calculate waste percentage using original algorithm.

        This method preserves the original logic from CostOptimizer for A/B testing.
        """
        total_tokens = input_tokens + output_tokens

        if total_tokens > 0:
            output_ratio = output_tokens / total_tokens
            if output_ratio < 0.1:  # Output ratio below 10%
                input_waste = (1 - output_ratio) * 50  # Max 50% waste
            else:
                input_waste = 0
        else:
            input_waste = 0

        return min(input_waste, 100.0)


class ParameterizedEfficiencyCalculator:
    """
    Parameterized efficiency calculator (v2.0).

    This version uses configurable thresholds and task-type-aware calculations.
    """

    version = "v2.0"

    # Task-type-specific threshold adjustments
    TASK_TYPE_ADJUSTMENTS: dict[TaskType, dict[str, Any]] = {
        TaskType.GENERAL: {},
        TaskType.CODE_GENERATION: {
            # Code generation typically has higher output ratio
            "output_ratio_ideal_range": (40.0, 60.0),
            "output_ratio_good_range": (30.0, 70.0),
            # Code generation can have lower cost per request
            "cost_per_request_excellent": 0.005,
        },
        TaskType.DOCUMENT_ANALYSIS: {
            # Document analysis has higher input ratio (normal)
            "output_ratio_ideal_range": (15.0, 35.0),
            "output_ratio_good_range": (10.0, 50.0),
            "output_ratio_acceptable_min": 5.0,
            # Lower waste threshold for document analysis
            "waste_output_ratio_threshold": 0.05,
        },
        TaskType.CONVERSATION: {
            # Conversation tasks have balanced ratio
            "output_ratio_ideal_range": (30.0, 50.0),
        },
    }

    def calculate_efficiency_score(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
        task_type: TaskType = TaskType.GENERAL,
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """
        Calculate efficiency score using parameterized algorithm.

        Args:
            tokens: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            requests: Number of requests.
            total_cost: Total cost.
            task_type: Task type for threshold adjustment.
            thresholds: Thresholds configuration.

        Returns:
            Efficiency score (0-100).
        """
        thresholds = thresholds or get_thresholds()

        # Get task-type-specific adjustments
        adjustments = self.TASK_TYPE_ADJUSTMENTS.get(task_type, {})

        # Apply adjustments to thresholds
        ideal_range = adjustments.get(
            "output_ratio_ideal_range", thresholds.output_ratio.ideal_range
        )
        good_range = adjustments.get(
            "output_ratio_good_range", thresholds.output_ratio.good_range
        )
        acceptable_min = adjustments.get(
            "output_ratio_acceptable_min", thresholds.output_ratio.acceptable_min
        )

        cost_excellent = adjustments.get(
            "cost_per_request_excellent", thresholds.cost_per_request.excellent
        )
        cost_good = adjustments.get(
            "cost_per_request_good", thresholds.cost_per_request.good
        )
        cost_acceptable = adjustments.get(
            "cost_per_request_acceptable", thresholds.cost_per_request.acceptable
        )

        # Base score
        efficiency_score = thresholds.base_score

        # Factor 1: Output ratio
        if tokens > 0:
            output_ratio = (output_tokens / tokens) * 100
            if ideal_range[0] <= output_ratio <= ideal_range[1]:
                efficiency_score += thresholds.output_ratio.ideal_score_bonus
            elif good_range[0] <= output_ratio <= good_range[1]:
                efficiency_score += thresholds.output_ratio.good_score_bonus
            elif output_ratio >= acceptable_min:
                efficiency_score += thresholds.output_ratio.acceptable_score_bonus

        # Factor 2: Cost efficiency
        if requests > 0:
            avg_cost = total_cost / requests
            if avg_cost < cost_excellent:
                efficiency_score += thresholds.cost_per_request.excellent_score_bonus
            elif avg_cost < cost_good:
                efficiency_score += thresholds.cost_per_request.good_score_bonus
            elif avg_cost < cost_acceptable:
                efficiency_score += thresholds.cost_per_request.acceptable_score_bonus

        # Factor 3: Request efficiency
        if requests > 0:
            avg_tokens = tokens / requests
            eff_range = thresholds.avg_tokens_per_request.efficient_range
            acc_range = thresholds.avg_tokens_per_request.acceptable_range

            if eff_range[0] <= avg_tokens <= eff_range[1]:
                efficiency_score += thresholds.avg_tokens_per_request.efficient_score_bonus
            elif acc_range[0] <= avg_tokens <= acc_range[1]:
                efficiency_score += thresholds.avg_tokens_per_request.acceptable_score_bonus

        return min(efficiency_score, 100.0)

    def calculate_waste_percentage(
        self,
        input_tokens: int,
        output_tokens: int,
        task_type: TaskType = TaskType.GENERAL,
        thresholds: EfficiencyThresholds | None = None,
    ) -> float:
        """
        Calculate waste percentage using parameterized algorithm.

        Args:
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            task_type: Task type for threshold adjustment.
            thresholds: Thresholds configuration.

        Returns:
            Waste percentage (0-100).
        """
        thresholds = thresholds or get_thresholds()
        adjustments = self.TASK_TYPE_ADJUSTMENTS.get(task_type, {})

        # Get waste threshold (document analysis has lower threshold)
        waste_threshold = adjustments.get(
            "waste_output_ratio_threshold",
            thresholds.waste_calculation.output_ratio_threshold,
        )

        total_tokens = input_tokens + output_tokens

        if total_tokens > 0:
            output_ratio = output_tokens / total_tokens
            if output_ratio < waste_threshold:
                # Calculate waste based on deviation from threshold
                waste = (waste_threshold - output_ratio) * thresholds.waste_calculation.waste_coefficient / waste_threshold
                input_waste = min(waste, thresholds.waste_calculation.waste_coefficient)
            else:
                input_waste = 0
        else:
            input_waste = 0

        return min(input_waste, 100.0)


class EfficiencyAlgorithmRegistry:
    """
    Registry for efficiency algorithm versions.

    Supports:
        - Multiple version registration
        - Version selection via parameter or configuration
        - A/B testing with gradual rollout
        - Quick rollback

    Example usage:
        registry = EfficiencyAlgorithmRegistry()
        registry.register("v1.0", LegacyEfficiencyCalculator())
        registry.register("v2.0", ParameterizedEfficiencyCalculator())

        calculator = registry.get_calculator("v2.0")
        score = calculator.calculate_efficiency_score(...)
    """

    def __init__(self):
        """Initialize the registry."""
        self._versions: dict[str, EfficiencyCalculator] = {}
        self._default_version = "v1.0"

    def register(self, version: str, calculator: EfficiencyCalculator) -> None:
        """
        Register an efficiency calculator version.

        Args:
            version: Version string (e.g., "v1.0", "v2.0")
            calculator: Calculator instance
        """
        if version in self._versions:
            logger.warning("Overwriting existing efficiency calculator version: %s", version)
        self._versions[version] = calculator
        logger.info("Registered efficiency calculator version: %s", version)

    def get_calculator(self, version: str | None = None) -> EfficiencyCalculator:
        """
        Get a calculator by version.

        Args:
            version: Version string, or None for default

        Returns:
            Calculator instance

        Raises:
            ValueError: If version is not registered
        """
        if version is None:
            version = self._default_version

        if version not in self._versions:
            logger.warning(
                "Unknown efficiency algorithm version %s, falling back to default %s",
                version,
                self._default_version,
            )
            version = self._default_version

        if version not in self._versions:
            raise ValueError(f"No efficiency calculator registered for version: {version}")

        return self._versions[version]

    def get_default_version(self) -> str:
        """Get the default version."""
        return self._default_version

    def set_default_version(self, version: str) -> None:
        """Set the default version."""
        if version not in self._versions:
            raise ValueError(f"Cannot set default to unregistered version: {version}")
        self._default_version = version
        logger.info("Set default efficiency algorithm version: %s", version)

    def get_supported_versions(self) -> list[str]:
        """Get list of supported versions."""
        return list(self._versions.keys())


# Global registry instance
_global_registry: EfficiencyAlgorithmRegistry | None = None


def get_registry() -> EfficiencyAlgorithmRegistry:
    """
    Get the global efficiency algorithm registry.

    The registry is lazily initialized with default versions:
        - v1.0: LegacyEfficiencyCalculator (original algorithm)
        - v2.0: ParameterizedEfficiencyCalculator (new algorithm)

    Returns:
        EfficiencyAlgorithmRegistry instance
    """
    global _global_registry

    if _global_registry is None:
        _global_registry = EfficiencyAlgorithmRegistry()
        # Register default versions
        _global_registry.register("v1.0", LegacyEfficiencyCalculator())
        _global_registry.register("v2.0", ParameterizedEfficiencyCalculator())
        # Default to v1.0 for gradual rollout
        _global_registry.set_default_version("v1.0")

    return _global_registry


def get_algorithm_version(tenant_id: int | None = None) -> str:
    """
    Determine the algorithm version for a tenant.

    Priority:
        1. Environment variable: OPENACE_EFFICIENCY_ALGORITHM_VERSION
        2. A/B test grouping: tenant_id % 10 < 1 → v2.0
        3. Default: v1.0

    Args:
        tenant_id: Optional tenant ID for A/B test grouping

    Returns:
        Algorithm version string
    """
    import os

    # Check environment variable
    env_version = os.environ.get("OPENACE_EFFICIENCY_ALGORITHM_VERSION")
    if env_version:
        registry = get_registry()
        if env_version in registry.get_supported_versions():
            return env_version
        logger.warning(
            "Unknown OPENACE_EFFICIENCY_ALGORITHM_VERSION=%s, using default",
            env_version,
        )

    # A/B test grouping (10% traffic to v2.0)
    if tenant_id is not None and tenant_id % 10 < 1:
        return "v2.0"

    # Default version
    registry = get_registry()
    return registry.get_default_version()
