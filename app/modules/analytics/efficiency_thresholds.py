"""
Efficiency Thresholds Configuration for CostOptimizer.

This module provides configurable thresholds for efficiency score calculation.
All thresholds are documented with their source and rationale.

Sources:
- EXPERIENCE: Based on operational experience and empirical observation
- HEURISTIC: Based on heuristic rules or reasonable defaults
- TO_BE_VALIDATED: Needs validation through A/B testing or data analysis
"""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ThresholdSource:
    """Threshold source types for documentation."""

    EXPERIENCE = "EXPERIENCE"  # Based on operational experience
    HEURISTIC = "HEURISTIC"  # Based on heuristic rules
    TO_BE_VALIDATED = "TO_BE_VALIDATED"  # Needs validation


@dataclass
class OutputRatioThresholds:
    """
    Thresholds for output ratio (output_tokens / total_tokens).

    Source: HEURISTIC
    Rationale:
        - 30-50% output ratio indicates good balance between input and output
        - Below 20% suggests verbose prompts or small responses
        - These thresholds are based on general LLM usage patterns
    Limitation:
        - Does not account for task type differences (e.g., document analysis)
        - Should be validated with real usage data
    """

    ideal_range: tuple[float, float] = (30.0, 50.0)  # Ideal output ratio %
    good_range: tuple[float, float] = (20.0, 60.0)  # Good output ratio %
    acceptable_min: float = 10.0  # Minimum acceptable output ratio %

    # Score adjustments
    ideal_score_bonus: float = 20.0
    good_score_bonus: float = 15.0
    acceptable_score_bonus: float = 10.0


@dataclass
class CostPerRequestThresholds:
    """
    Thresholds for cost per request.

    Source: EXPERIENCE
    Rationale:
        - $0.01/request is excellent for most tasks
        - $0.05/request is good for moderate complexity
        - $0.10/request is acceptable but worth optimizing
    Limitation:
        - Does not account for model pricing differences
        - Task complexity affects reasonable cost
    """

    excellent: float = 0.01  # Excellent cost per request ($)
    good: float = 0.05  # Good cost per request ($)
    acceptable: float = 0.10  # Acceptable cost per request ($)

    # Score adjustments
    excellent_score_bonus: float = 15.0
    good_score_bonus: float = 10.0
    acceptable_score_bonus: float = 5.0


@dataclass
class AvgTokensPerRequestThresholds:
    """
    Thresholds for average tokens per request.

    Source: HEURISTIC
    Rationale:
        - 500-2000 tokens is efficient for most tasks
        - 200-5000 tokens is acceptable range
        - Outside this range may indicate inefficiency
    Limitation:
        - Does not account for task type (e.g., code generation vs. chat)
    """

    efficient_range: tuple[int, int] = (500, 2000)
    acceptable_range: tuple[int, int] = (200, 5000)

    # Score adjustments
    efficient_score_bonus: float = 5.0
    acceptable_score_bonus: float = 3.0


@dataclass
class WasteCalculationThresholds:
    """
    Thresholds for waste calculation.

    Source: TO_BE_VALIDATED
    Rationale:
        - Output ratio below 10% suggests potential waste
        - Coefficient 50 means max 50% waste for 0% output
        - These values need validation through data analysis
    Limitation:
        - Assumes low output ratio = waste, which may not hold for all task types
        - Linear calculation may not reflect actual waste
    """

    output_ratio_threshold: float = 0.1  # Below this triggers waste calculation
    waste_coefficient: float = 50.0  # Max waste percentage for 0% output


@dataclass
class EfficiencyThresholds:
    """
    Complete thresholds configuration for efficiency calculation.

    This configuration supports environment variable overrides via
    OPENACE_EFFICIENCY_* prefix.

    Priority: Environment variables > Default values

    Example environment variables:
        OPENACE_EFFICIENCY_BASE_SCORE=60.0
        OPENACE_EFFICIENCY_OUTPUT_RATIO_IDEAL_MIN=30.0
        OPENACE_EFFICIENCY_COST_PER_REQUEST_EXCELLENT=0.01
    """

    version: str = "2.0"

    # Base efficiency score
    # Source: EXPERIENCE
    # Rationale: 60 points provides a neutral baseline; adjustments add/subtract
    base_score: float = 60.0

    output_ratio: OutputRatioThresholds = field(default_factory=OutputRatioThresholds)
    cost_per_request: CostPerRequestThresholds = field(default_factory=CostPerRequestThresholds)
    avg_tokens_per_request: AvgTokensPerRequestThresholds = field(
        default_factory=AvgTokensPerRequestThresholds
    )
    waste_calculation: WasteCalculationThresholds = field(
        default_factory=WasteCalculationThresholds
    )

    @classmethod
    def from_env(cls) -> "EfficiencyThresholds":
        """
        Load thresholds from environment variables.

        Environment variable naming convention:
            OPENACE_EFFICIENCY_{SECTION}_{FIELD}

        Examples:
            OPENACE_EFFICIENCY_BASE_SCORE
            OPENACE_EFFICIENCY_OUTPUT_RATIO_IDEAL_MIN
            OPENACE_EFFICIENCY_COST_PER_REQUEST_EXCELLENT
        """
        thresholds = cls()

        # Base score
        if "OPENACE_EFFICIENCY_BASE_SCORE" in os.environ:
            thresholds.base_score = cls._read_float_env(
                "OPENACE_EFFICIENCY_BASE_SCORE", thresholds.base_score
            )

        # Output ratio thresholds
        if "OPENACE_EFFICIENCY_OUTPUT_RATIO_IDEAL_MIN" in os.environ:
            ideal_min = cls._read_float_env(
                "OPENACE_EFFICIENCY_OUTPUT_RATIO_IDEAL_MIN",
                thresholds.output_ratio.ideal_range[0],
            )
            ideal_max = cls._read_float_env(
                "OPENACE_EFFICIENCY_OUTPUT_RATIO_IDEAL_MAX",
                thresholds.output_ratio.ideal_range[1],
            )
            thresholds.output_ratio.ideal_range = (ideal_min, ideal_max)

        if "OPENACE_EFFICIENCY_OUTPUT_RATIO_GOOD_MIN" in os.environ:
            good_min = cls._read_float_env(
                "OPENACE_EFFICIENCY_OUTPUT_RATIO_GOOD_MIN",
                thresholds.output_ratio.good_range[0],
            )
            good_max = cls._read_float_env(
                "OPENACE_EFFICIENCY_OUTPUT_RATIO_GOOD_MAX",
                thresholds.output_ratio.good_range[1],
            )
            thresholds.output_ratio.good_range = (good_min, good_max)

        # Cost per request thresholds
        if "OPENACE_EFFICIENCY_COST_PER_REQUEST_EXCELLENT" in os.environ:
            thresholds.cost_per_request.excellent = cls._read_float_env(
                "OPENACE_EFFICIENCY_COST_PER_REQUEST_EXCELLENT",
                thresholds.cost_per_request.excellent,
            )

        if "OPENACE_EFFICIENCY_COST_PER_REQUEST_GOOD" in os.environ:
            thresholds.cost_per_request.good = cls._read_float_env(
                "OPENACE_EFFICIENCY_COST_PER_REQUEST_GOOD",
                thresholds.cost_per_request.good,
            )

        if "OPENACE_EFFICIENCY_COST_PER_REQUEST_ACCEPTABLE" in os.environ:
            thresholds.cost_per_request.acceptable = cls._read_float_env(
                "OPENACE_EFFICIENCY_COST_PER_REQUEST_ACCEPTABLE",
                thresholds.cost_per_request.acceptable,
            )

        return thresholds

    @staticmethod
    def _read_float_env(name: str, default: float) -> float:
        """Read a float from environment variable with validation."""
        raw_value = os.environ.get(name, "").strip()
        if not raw_value:
            return default
        try:
            parsed = float(raw_value)
        except ValueError:
            logger.warning(
                "Invalid efficiency threshold %s=%r; using default %s",
                name,
                raw_value,
                default,
            )
            return default
        return parsed if parsed > 0 else default

    def validate(self) -> tuple[bool, list[str]]:
        """
        Validate thresholds for logical consistency.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: list[str] = []

        # Base score must be in valid range
        if not (0 <= self.base_score <= 100):
            errors.append(f"base_score must be in [0, 100], got {self.base_score}")

        # Output ratio: ideal_range should be within good_range
        ideal_min, ideal_max = self.output_ratio.ideal_range
        good_min, good_max = self.output_ratio.good_range

        if ideal_min >= ideal_max:
            errors.append(
                f"output_ratio.ideal_range min must be < max, got [{ideal_min}, {ideal_max}]"
            )

        if good_min >= good_max:
            errors.append(
                f"output_ratio.good_range min must be < max, got [{good_min}, {good_max}]"
            )

        if not (good_min <= ideal_min and ideal_max <= good_max):
            errors.append(
                f"output_ratio.ideal_range [{ideal_min}, {ideal_max}] "
                f"must be within good_range [{good_min}, {good_max}]"
            )

        if ideal_min < self.output_ratio.acceptable_min:
            errors.append(
                f"output_ratio.ideal_range min ({ideal_min}) "
                f"must be >= acceptable_min ({self.output_ratio.acceptable_min})"
            )

        # Cost per request: excellent < good < acceptable
        if not (
            self.cost_per_request.excellent
            < self.cost_per_request.good
            < self.cost_per_request.acceptable
        ):
            errors.append(
                f"cost_per_request thresholds must be ordered: "
                f"excellent ({self.cost_per_request.excellent}) < "
                f"good ({self.cost_per_request.good}) < "
                f"acceptable ({self.cost_per_request.acceptable})"
            )

        # Avg tokens per request: efficient_range within acceptable_range
        eff_min, eff_max = self.avg_tokens_per_request.efficient_range
        acc_min, acc_max = self.avg_tokens_per_request.acceptable_range

        if eff_min >= eff_max:
            errors.append(
                f"avg_tokens_per_request.efficient_range min must be < max, "
                f"got [{eff_min}, {eff_max}]"
            )

        if acc_min >= acc_max:
            errors.append(
                f"avg_tokens_per_request.acceptable_range min must be < max, "
                f"got [{acc_min}, {acc_max}]"
            )

        if not (acc_min <= eff_min and eff_max <= acc_max):
            errors.append(
                f"avg_tokens_per_request.efficient_range [{eff_min}, {eff_max}] "
                f"must be within acceptable_range [{acc_min}, {acc_max}]"
            )

        # Waste calculation thresholds
        if not (0 <= self.waste_calculation.output_ratio_threshold <= 1):
            errors.append(
                f"waste_calculation.output_ratio_threshold must be in [0, 1], "
                f"got {self.waste_calculation.output_ratio_threshold}"
            )

        if not (0 <= self.waste_calculation.waste_coefficient <= 100):
            errors.append(
                f"waste_calculation.waste_coefficient must be in [0, 100], "
                f"got {self.waste_calculation.waste_coefficient}"
            )

        return len(errors) == 0, errors


# Default thresholds instance
DEFAULT_THRESHOLDS = EfficiencyThresholds()


def get_thresholds() -> EfficiencyThresholds:
    """
    Get thresholds configuration.

    Priority: Environment variables > Default values

    Returns:
        EfficiencyThresholds instance
    """
    # Check if any efficiency env vars are set
    efficiency_env_vars = [k for k in os.environ if k.startswith("OPENACE_EFFICIENCY_")]

    if efficiency_env_vars:
        thresholds = EfficiencyThresholds.from_env()
        is_valid, errors = thresholds.validate()
        if not is_valid:
            logger.error(
                "Efficiency thresholds validation failed, using defaults: %s",
                "; ".join(errors),
            )
            return DEFAULT_THRESHOLDS
        logger.info("Loaded efficiency thresholds from environment: %s", efficiency_env_vars)
        return thresholds

    return DEFAULT_THRESHOLDS
