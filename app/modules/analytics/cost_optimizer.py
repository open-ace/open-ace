"""
Open ACE - Cost Optimizer Module

Analyzes usage patterns and provides cost optimization suggestions.
Identifies opportunities for cost savings and efficiency improvements.

Algorithm Versions:
    v1.0 (Legacy): Original algorithm with hardcoded thresholds
    v2.0 (Parameterized): Configurable thresholds with task-type awareness

Task Types:
    GENERAL: Default for unclassified tasks
    CODE_GENERATION: Code generation tasks (higher output ratio)
    DOCUMENT_ANALYSIS: Document analysis tasks (higher input ratio)
    CONVERSATION: Chat/conversation tasks (balanced ratio)
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, cast

from app.modules.analytics.efficiency_registry import get_algorithm_version, get_registry
from app.modules.analytics.efficiency_thresholds import get_thresholds
from app.modules.analytics.roi_calculator import ROICalculator
from app.modules.analytics.task_type_inferencer import InferenceResult, TaskType, TaskTypeInferencer
from app.repositories.database import Database
from app.utils.cache import cached

logger = logging.getLogger(__name__)


def _normalize_tenant_id(value: object) -> int | None:
    """Normalize a tenant identifier to a positive integer.

    Mirrors ``roi_calculator._normalize_tenant_id``. ``None``/0/blank
    collapse to ``None`` so single-tenant deployments keep their original
    query shape (no tenant filter), while multi-tenant callers always scope.
    """
    if value in (None, "", 0, "0"):
        return None
    try:
        tenant_id = int(cast("Any", value))
    except (TypeError, ValueError):
        return None
    return tenant_id if tenant_id > 0 else None


# Thread pool for parallel queries
_executor = ThreadPoolExecutor(max_workers=4)


class OptimizationType(Enum):
    """Types of optimization suggestions."""

    MODEL_SWITCH = "model_switch"
    USAGE_PATTERN = "usage_pattern"
    QUOTA_ADJUSTMENT = "quota_adjustment"
    TOOL_CONSOLIDATION = "tool_consolidation"
    TIME_OPTIMIZATION = "time_optimization"
    TOKEN_OPTIMIZATION = "token_optimization"


class Priority(Enum):
    """Priority levels for suggestions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class OptimizationSuggestion:
    """A cost optimization suggestion."""

    suggestion_id: str
    suggestion_type: str
    title: str
    description: str
    potential_savings: float
    priority: str
    action_items: list[str] = field(default_factory=list)
    affected_users: list[int] = field(default_factory=list)
    affected_tools: list[str] = field(default_factory=list)
    implementation_effort: str = "medium"  # low, medium, high
    current_cost: float = 0.0
    optimized_cost: float = 0.0
    savings_percentage: float = 0.0
    # Language-neutral interpolation params for frontend localization
    # (e.g. {"model": "...", "cheaper_model": "...", "avg_tokens": "..."}).
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    def to_dict(self) -> dict:
        return {
            "suggestion_id": self.suggestion_id,
            "suggestion_type": self.suggestion_type,
            "title": self.title,
            "description": self.description,
            "potential_savings": round(self.potential_savings, 2),
            "priority": self.priority,
            "action_items": self.action_items,
            "params": self.params,
            "affected_users": self.affected_users,
            "affected_tools": self.affected_tools,
            "implementation_effort": self.implementation_effort,
            "current_cost": round(self.current_cost, 4),
            "optimized_cost": round(self.optimized_cost, 4),
            "savings_percentage": round(self.savings_percentage, 2),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class UsagePattern:
    """Usage pattern analysis result."""

    pattern_type: str
    description: str
    frequency: int
    impact: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "frequency": self.frequency,
            "impact": self.impact,
            "details": self.details,
        }


class CostOptimizer:
    """Cost optimization analyzer for AI usage."""

    # Class-level singleton: reuse ROICalculator pricing logic
    _pricing_calculator: ROICalculator | None = None

    # Model hierarchy (expensive to cheap) - used for model switch suggestions
    MODEL_HIERARCHY = {
        "claude": [
            "claude-3-opus",
            "claude-3-5-sonnet",
            "claude-3-sonnet",
            "claude-3-5-haiku",
            "claude-3-haiku",
        ],
        "qwen": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "gpt": ["gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    }

    # Thresholds
    SHORT_REQUEST_THRESHOLD = 500  # tokens
    HIGH_COST_USER_THRESHOLD = 100.0  # USD
    LOW_USAGE_THRESHOLD = 0.2  # 20% of average

    @classmethod
    def _get_pricing_calculator(cls) -> ROICalculator:
        """Get or create the shared ROICalculator instance for unified pricing."""
        if cls._pricing_calculator is None:
            cls._pricing_calculator = ROICalculator()
        return cls._pricing_calculator

    def __init__(self, db: Database | None = None):
        """
        Initialize Cost Optimizer.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    @cached(ttl=120, key_prefix="cost", skip_args=[0])
    def analyze(
        self, days: int = 30, tool_name: str | None = None, tenant_id: int | None = None
    ) -> list[OptimizationSuggestion]:
        """
        Analyze usage and generate optimization suggestions.

        Args:
            days: Number of days to analyze.
            tool_name: Optional tool name filter.
            tenant_id: Optional tenant scope (caller's tenant). Included in the
                cache key so one tenant never reads another's aggregate.

        Returns:
            List of OptimizationSuggestion objects.
        """
        suggestions = []

        end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        # Get usage data
        usage_data = self._get_usage_data(
            start_date, end_date, tool_name=tool_name, tenant_id=tenant_id
        )

        # Analyze different aspects
        suggestions.extend(self._analyze_model_usage(usage_data, start_date, end_date))
        suggestions.extend(self._analyze_usage_patterns(usage_data))
        suggestions.extend(self._analyze_quota_efficiency(usage_data))
        suggestions.extend(self._analyze_tool_usage(usage_data))
        suggestions.extend(self._analyze_token_efficiency(usage_data))

        # Sort by potential savings
        suggestions.sort(key=lambda x: x.potential_savings, reverse=True)

        return suggestions

    def _get_usage_data(
        self,
        start_date: str,
        end_date: str,
        tool_name: str | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """Get comprehensive usage data.

        Optimized: Uses a single query to fetch all data, then aggregates in Python.
        This reduces database round trips from 4 to 1.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            tenant_id: Optional tenant scope.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)

        # Build query with optional tenant filter
        query = """
            SELECT tool_name, models_used as model, date,
                   input_tokens, output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params: list[Any] = [start_date, end_date]

        if normalized_tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(normalized_tenant_id)

        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)

        # Single query to get all raw data
        all_data = self.db.fetch_all(query, tuple(params))

        # Aggregate in Python
        total_requests = len(all_data)
        total_input_tokens = sum(r.get("input_tokens") or 0 for r in all_data)
        total_output_tokens = sum(r.get("output_tokens") or 0 for r in all_data)

        overall = {
            "total_requests": total_requests,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
        }

        # By model aggregation
        model_stats: dict[tuple, dict] = {}
        for row in all_data:
            key = (row.get("tool_name"), row.get("model"))
            if key not in model_stats:
                model_stats[key] = {
                    "tool_name": row.get("tool_name"),
                    "model": row.get("model"),
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            model_stats[key]["requests"] += 1
            model_stats[key]["input_tokens"] += row.get("input_tokens") or 0
            model_stats[key]["output_tokens"] += row.get("output_tokens") or 0

        # Calculate avg_tokens_per_request and convert to list
        by_model = []
        for stats in model_stats.values():
            stats["avg_tokens_per_request"] = (
                (stats["input_tokens"] + stats["output_tokens"]) / stats["requests"]
                if stats["requests"] > 0
                else 0
            )
            by_model.append(stats)

        # Note: user_id and timestamp columns don't exist in daily_usage table
        # So by_user and by_hour will be empty lists
        by_user: list[dict[str, Any]] = []
        by_hour: list[dict[str, Any]] = []

        return {
            "overall": overall,
            "by_model": by_model,
            "by_user": by_user,
            "by_hour": by_hour,
        }

    def _analyze_model_usage(
        self, data: dict, start_date: str, end_date: str
    ) -> list[OptimizationSuggestion]:
        """Analyze model usage for optimization opportunities."""
        suggestions = []

        for row in data["by_model"]:
            model = row.get("model") or "unknown"
            avg_tokens = row.get("avg_tokens_per_request") or 0
            row.get("requests") or 0
            input_tokens = row.get("input_tokens") or 0
            output_tokens = row.get("output_tokens") or 0

            # Check for expensive model on simple tasks
            if self._is_expensive_model(model) and avg_tokens < self.SHORT_REQUEST_THRESHOLD:
                cheaper_model = self._find_cheaper_alternative(model)
                if cheaper_model:
                    savings = self._calculate_model_savings(
                        model, cheaper_model, input_tokens, output_tokens
                    )

                    if savings > 1.0:  # Only suggest if savings > $1
                        suggestions.append(
                            OptimizationSuggestion(
                                suggestion_id=f"model_switch_{model}_{cheaper_model}",
                                suggestion_type=OptimizationType.MODEL_SWITCH.value,
                                title=f"Switch to {cheaper_model} for simple tasks",
                                description=f"Model {model} is used for short requests (avg {avg_tokens:.0f} tokens). "
                                f"Consider using {cheaper_model} for tasks under {self.SHORT_REQUEST_THRESHOLD} tokens.",
                                potential_savings=savings,
                                priority=Priority.HIGH.value,
                                action_items=[
                                    f"Route requests under {self.SHORT_REQUEST_THRESHOLD} tokens to {cheaper_model}",
                                    "Keep complex tasks on current model",
                                    "Implement automatic model selection based on task complexity",
                                ],
                                params={
                                    "model": model,
                                    "cheaper_model": cheaper_model,
                                    "avg_tokens": f"{avg_tokens:.0f}",
                                    "threshold": self.SHORT_REQUEST_THRESHOLD,
                                },
                                affected_tools=(
                                    [row.get("tool_name")] if row.get("tool_name") else []
                                ),
                                current_cost=self._calculate_cost(
                                    model, input_tokens, output_tokens
                                ),
                                optimized_cost=self._calculate_cost(
                                    cheaper_model, input_tokens, output_tokens
                                ),
                                savings_percentage=self._savings_percentage(
                                    savings, input_tokens, output_tokens, model
                                ),
                            )
                        )

        return suggestions

    def _analyze_usage_patterns(self, data: dict) -> list[OptimizationSuggestion]:
        """Analyze usage patterns for optimization."""
        suggestions = []

        # Analyze peak hours
        if data["by_hour"]:
            peak_hours = sorted(data["by_hour"], key=lambda x: x["requests"], reverse=True)[:3]
            peak_hours_list = [h["hour"] for h in peak_hours]

            total_requests = data["overall"]["total_requests"] or 1
            peak_requests = sum(h["requests"] for h in peak_hours)
            peak_percentage = peak_requests / total_requests * 100

            if peak_percentage > 50:
                suggestions.append(
                    OptimizationSuggestion(
                        suggestion_id="time_optimization_peak",
                        suggestion_type=OptimizationType.TIME_OPTIMIZATION.value,
                        title="Optimize usage time distribution",
                        description=f"Peak hours ({', '.join(str(h) for h in peak_hours_list)}:00) concentrate {peak_percentage:.1f}% of requests. "
                        "Distributing usage more evenly can improve response times.",
                        potential_savings=0,  # Time optimization doesn't directly save money
                        priority=Priority.MEDIUM.value,
                        action_items=[
                            "Schedule batch tasks during off-peak hours",
                            "Implement request queuing for non-urgent tasks",
                            "Monitor response times and adjust scheduling",
                        ],
                        params={
                            "peak_hours": ", ".join(str(h) for h in peak_hours_list),
                            "peak_percentage": f"{peak_percentage:.1f}",
                        },
                    )
                )

        return suggestions

    def _analyze_quota_efficiency(self, data: dict) -> list[OptimizationSuggestion]:
        """Analyze quota allocation efficiency."""
        suggestions = []

        if data["by_user"]:
            total_tokens = sum(u["total_tokens"] or 0 for u in data["by_user"])
            user_count = len(data["by_user"])
            avg_tokens = total_tokens / user_count if user_count > 0 else 0

            # Find low usage users
            low_usage_users = [
                u["user_id"]
                for u in data["by_user"]
                if (u["total_tokens"] or 0) < avg_tokens * self.LOW_USAGE_THRESHOLD
            ]

            if len(low_usage_users) > user_count * 0.3:
                suggestions.append(
                    OptimizationSuggestion(
                        suggestion_id="quota_adjustment_low_usage",
                        suggestion_type=OptimizationType.QUOTA_ADJUSTMENT.value,
                        title="Optimize quota allocation",
                        description=f"Found {len(low_usage_users)} users with usage below 20% of average. "
                        "Consider reallocating unused quotas.",
                        potential_savings=0,
                        priority=Priority.LOW.value,
                        action_items=[
                            "Review quota settings for low-usage users",
                            "Consider quota pooling or reallocation",
                            "Implement quota expiration and recycling",
                        ],
                        params={
                            "low_usage_count": len(low_usage_users),
                            "usage_threshold": int(self.LOW_USAGE_THRESHOLD * 100),
                        },
                        affected_users=low_usage_users[:10],
                    )
                )

        return suggestions

    def _analyze_tool_usage(self, data: dict) -> list[OptimizationSuggestion]:
        """Analyze tool usage patterns."""
        suggestions = []

        tools = {row.get("tool_name") for row in data["by_model"] if row.get("tool_name")}

        if len(tools) > 2:
            suggestions.append(
                OptimizationSuggestion(
                    suggestion_id="tool_consolidation",
                    suggestion_type=OptimizationType.TOOL_CONSOLIDATION.value,
                    title="Consider tool consolidation",
                    description=f"Currently using {len(tools)} different AI tools. "
                    "Consolidation may enable volume discounts.",
                    potential_savings=0,
                    priority=Priority.LOW.value,
                    action_items=[
                        "Evaluate usage frequency and cost per tool",
                        "Negotiate volume discounts with providers",
                        "Consider standardizing on primary tools",
                    ],
                    params={
                        "tool_count": len(tools),
                    },
                    affected_tools=list(tools),
                )
            )

        return suggestions

    def _analyze_token_efficiency(self, data: dict) -> list[OptimizationSuggestion]:
        """Analyze token usage efficiency."""
        suggestions = []

        total_input = data["overall"]["total_input_tokens"] or 0
        total_output = data["overall"]["total_output_tokens"] or 0
        total_tokens = total_input + total_output

        if total_tokens > 0:
            output_ratio = total_output / total_tokens

            # High input ratio might indicate verbose prompts
            if output_ratio < 0.3:
                suggestions.append(
                    OptimizationSuggestion(
                        suggestion_id="token_optimization_input",
                        suggestion_type=OptimizationType.TOKEN_OPTIMIZATION.value,
                        title="Optimize prompt efficiency",
                        description=f"Output ratio is only {output_ratio * 100:.1f}%. "
                        "Consider optimizing prompts to reduce input tokens.",
                        potential_savings=total_input * 0.00001 * 0.3,  # Estimate 30% reduction
                        priority=Priority.MEDIUM.value,
                        action_items=[
                            "Review and optimize prompt templates",
                            "Remove unnecessary context from prompts",
                            "Use prompt caching where available",
                        ],
                        params={
                            "output_ratio": f"{output_ratio * 100:.1f}",
                        },
                    )
                )

        return suggestions

    def _is_expensive_model(self, model: str) -> bool:
        """Check if model is expensive."""
        expensive_models = ["claude-3-opus", "gpt-4", "qwen-max"]
        model_lower = model.lower() if model else ""
        return any(e in model_lower for e in expensive_models)

    def _find_cheaper_alternative(self, model: str) -> str | None:
        """Find a cheaper alternative model."""
        model_lower = model.lower() if model else ""

        for prefix, hierarchy in self.MODEL_HIERARCHY.items():
            if prefix in model_lower:
                try:
                    idx = next(i for i, m in enumerate(hierarchy) if m in model_lower)
                    if idx < len(hierarchy) - 1:
                        return hierarchy[idx + 1]
                except StopIteration:
                    pass

        return None

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for token usage using unified pricing."""
        pricing = self._get_pricing_calculator().get_model_pricing(model)
        return (
            input_tokens / 1000 * pricing.input_price + output_tokens / 1000 * pricing.output_price
        )

    def _calculate_model_savings(
        self, current_model: str, cheaper_model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calculate savings from switching models."""
        current_cost = self._calculate_cost(current_model, input_tokens, output_tokens)
        cheaper_cost = self._calculate_cost(cheaper_model, input_tokens, output_tokens)
        return current_cost - cheaper_cost

    def _savings_percentage(
        self, savings: float, input_tokens: int, output_tokens: int, model: str
    ) -> float:
        """Calculate savings percentage."""
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        return (savings / cost * 100) if cost > 0 else 0

    @cached(ttl=60, key_prefix="cost", skip_args=[0])
    def get_cost_trend(self, days: int = 30, tenant_id: int | None = None) -> list[dict[str, Any]]:
        """
        Get daily cost trend.

        Args:
            days: Number of days.
            tenant_id: Optional tenant scope (caller's tenant). Included in the
                cache key so one tenant never reads another's aggregate.

        Returns:
            List of daily cost data.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)

        end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        query = """
            SELECT date,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params: list[Any] = [start_date, end_date]

        if normalized_tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(normalized_tenant_id)

        query += " GROUP BY date ORDER BY date"

        rows = self.db.fetch_all(query, tuple(params))

        trend = []
        for row in rows:
            input_tokens = row.get("input_tokens") or 0
            output_tokens = row.get("output_tokens") or 0

            # Use average pricing for trend
            cost = input_tokens / 1000 * 0.005 + output_tokens / 1000 * 0.015

            trend.append(
                {
                    "date": row.get("date"),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": round(cost, 4),
                }
            )

        return trend

    def _calculate_efficiency_score(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
        task_type: TaskType = TaskType.GENERAL,
        algorithm_version: str | None = None,
    ) -> float:
        """
        Calculate efficiency score based on multiple factors.

        Supports algorithm versioning for A/B testing and gradual rollout.

        Args:
            tokens: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            requests: Number of requests.
            total_cost: Total cost.
            task_type: Task type for threshold adjustment (v2.0 only).
            algorithm_version: Algorithm version ("v1.0", "v2.0", or None for default).

        Returns:
            Efficiency score (0-100).

        Algorithm Versions:
            v1.0 (Legacy): Original algorithm with hardcoded thresholds.
                - Base score: 60 points
                - Output ratio 30-50%: +20 points
                - Cost per request < $0.01: +15 points
                - Avg tokens 500-2000: +5 points

            v2.0 (Parameterized): Configurable thresholds with task-type awareness.
                - Uses EfficiencyThresholds from environment or defaults
                - Adjusts thresholds based on task_type
                - See efficiency_thresholds.py for configuration options

        Note:
            This method differs from ROICalculator._calculate_efficiency_score:
            - ROICalculator uses cost-benefit ratio (estimated_savings / total_cost)
            - CostOptimizer uses cost efficiency (avg_cost_per_request thresholds)
            This difference is intentional: CostOptimizer focuses on raw cost metrics,
            while ROICalculator incorporates estimated labor savings.
        """
        registry = get_registry()
        version = algorithm_version or registry.get_default_version()

        try:
            calculator = registry.get_calculator(version)
            thresholds = get_thresholds()
            return calculator.calculate_efficiency_score(
                tokens=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                requests=requests,
                total_cost=total_cost,
                task_type=task_type,
                thresholds=thresholds,
            )
        except Exception as e:
            # Log as warning since we fallback to v1.0 and system remains functional
            logger.warning(
                "Efficiency score calculation failed for version %s: %s, falling back to v1.0",
                version,
                e,
            )
            # Fallback to v1.0 algorithm
            return self._calculate_efficiency_score_v1(
                tokens, input_tokens, output_tokens, requests, total_cost
            )

    def _calculate_efficiency_score_v1(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
    ) -> float:
        """
        Legacy efficiency score calculation (v1.0).

        Preserved for fallback and A/B testing baseline.
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

    def _calculate_total_cost(self, by_model: list) -> float:
        """
        Calculate total cost from model usage data.

        Args:
            by_model: List of model usage data.

        Returns:
            Total cost in USD.
        """
        total_cost = 0.0
        for row in by_model:
            model = row.get("model") or "unknown"
            input_tokens = row.get("input_tokens") or 0
            output_tokens = row.get("output_tokens") or 0
            total_cost += self._calculate_cost(model, input_tokens, output_tokens)
        return total_cost

    def _calculate_waste_percentage(
        self,
        input_tokens: int,
        output_tokens: int,
        task_type: TaskType = TaskType.GENERAL,
        algorithm_version: str | None = None,
    ) -> float:
        """
        Calculate waste percentage based on input/output imbalance.

        Supports algorithm versioning for A/B testing and gradual rollout.

        Args:
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            task_type: Task type for threshold adjustment (v2.0 only).
            algorithm_version: Algorithm version ("v1.0", "v2.0", or None for default).

        Returns:
            Waste percentage (0-100).

        Algorithm Versions:
            v1.0 (Legacy): Simple linear calculation.
                - Output ratio < 10%: waste = (1 - output_ratio) * 50
                - Output ratio >= 10%: no waste

            v2.0 (Parameterized): Configurable with task-type awareness.
                - Threshold configurable via OPENACE_EFFICIENCY_WASTE_OUTPUT_RATIO_THRESHOLD
                - DOCUMENT_ANALYSIS tasks have lower threshold (0.05 vs 0.10)
                - Non-linear calculation based on deviation from threshold

        Limitation:
            Assumes low output ratio = waste, which may not hold for all task types.
            v2.0 attempts to address this with task-type-specific thresholds.
        """
        registry = get_registry()
        version = algorithm_version or registry.get_default_version()

        try:
            calculator = registry.get_calculator(version)
            thresholds = get_thresholds()
            return calculator.calculate_waste_percentage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                task_type=task_type,
                thresholds=thresholds,
            )
        except Exception as e:
            # Log as warning since we fallback to v1.0 and system remains functional
            logger.warning(
                "Waste percentage calculation failed for version %s: %s, falling back to v1.0",
                version,
                e,
            )
            # Fallback to v1.0 algorithm
            return self._calculate_waste_percentage_v1(input_tokens, output_tokens)

    def _calculate_waste_percentage_v1(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Legacy waste percentage calculation (v1.0).

        Preserved for fallback and A/B testing baseline.
        """
        total_tokens = input_tokens + output_tokens

        # Factor: Input/output imbalance waste
        # Ideal output ratio should be 20-50%, below this is considered waste
        if total_tokens > 0:
            output_ratio = output_tokens / total_tokens
            if output_ratio < 0.1:  # Output ratio below 10%
                input_waste = (1 - output_ratio) * 50  # Max 50% waste
            else:
                input_waste = 0
        else:
            input_waste = 0

        return min(input_waste, 100.0)

    def _generate_recommendations(
        self,
        efficiency_score: float,
        output_ratio: float,
        avg_cost_per_request: float,
        avg_tokens_per_request: float,
        model_distribution: dict,
    ) -> list[dict[str, Any]]:
        """
        Generate efficiency optimization recommendations.

        Produces language-neutral structured items ``{"type", "params"}`` so the
        frontend can localize them. ``recommendation_type`` values are stable
        identifiers consumed by the i18n layer (e.g. ``low_efficiency``).

        Args:
            efficiency_score: Overall efficiency score.
            output_ratio: Output ratio percentage.
            avg_cost_per_request: Average cost per request.
            avg_tokens_per_request: Average tokens per request.
            model_distribution: Model usage distribution.

        Returns:
            List of structured recommendation items.
        """
        recommendations: list[dict[str, Any]] = []

        # Low efficiency score
        if efficiency_score < 70:
            recommendations.append(
                {
                    "type": "low_efficiency",
                    "params": {"efficiency_score": f"{efficiency_score:.1f}"},
                }
            )

        # Low output ratio
        if output_ratio < 10:
            recommendations.append(
                {"type": "low_output_ratio", "params": {"output_ratio": f"{output_ratio:.1f}"}}
            )

        # High cost per request
        if avg_cost_per_request > 0.10:
            recommendations.append(
                {
                    "type": "high_cost_per_request",
                    "params": {"avg_cost_per_request": f"{avg_cost_per_request:.4f}"},
                }
            )

        # High average tokens per request
        if avg_tokens_per_request > 5000:
            recommendations.append(
                {
                    "type": "high_avg_tokens",
                    "params": {"avg_tokens_per_request": f"{avg_tokens_per_request:.0f}"},
                }
            )

        # High model concentration
        if model_distribution:
            top_model = max(model_distribution, key=lambda k: model_distribution.get(k, 0))
            total_tokens = sum(model_distribution.values())
            if total_tokens > 0:
                top_share = model_distribution[top_model] / total_tokens
                if top_share > 0.9:
                    recommendations.append(
                        {
                            "type": "high_model_concentration",
                            "params": {
                                "top_model": top_model,
                                "top_share": f"{top_share * 100:.1f}",
                            },
                        }
                    )

        # Default positive recommendation
        if not recommendations:
            recommendations.append({"type": "healthy", "params": {}})

        return recommendations

    @cached(ttl=120, key_prefix="cost", skip_args=[0])
    def get_efficiency_report(
        self,
        days: int = 30,
        tool_name: str | None = None,
        tenant_id: int | None = None,
        task_type: str | None = None,
        algorithm_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Get efficiency analysis report.

        Supports algorithm versioning for A/B testing and gradual rollout.

        Args:
            days: Number of days to analyze.
            tool_name: Optional tool name filter.
            tenant_id: Optional tenant scope (caller's tenant). Included in the
                cache key so one tenant never reads another's aggregate.
            task_type: Optional task type filter (GENERAL, CODE_GENERATION,
                DOCUMENT_ANALYSIS, CONVERSATION). If not provided, uses GENERAL.
            algorithm_version: Algorithm version ("v1.0", "v2.0", or None for
                default/auto selection).

        Returns:
            Dict with efficiency metrics including:
                - algorithm_version: The algorithm version used
                - applied_task_type: The task type applied
                - inference_confidence: Task type inference confidence (0-100)
                - overall_efficiency: Efficiency score (0-100)
                - waste_percentage: Waste percentage (0-100)
                - And other existing metrics
        """
        # Determine algorithm version
        resolved_version = algorithm_version
        if resolved_version is None or resolved_version == "auto":
            resolved_version = get_algorithm_version(tenant_id)

        # Determine task type
        inference_result: InferenceResult
        if task_type:
            try:
                resolved_task_type = TaskType(task_type.upper())
                inference_result = InferenceResult(
                    task_type=resolved_task_type,
                    confidence=100.0,
                    matched_patterns=[],
                )
            except ValueError:
                logger.warning("Unknown task_type %s, using GENERAL", task_type)
                inference_result = TaskTypeInferencer.infer(None)
        else:
            # Infer from data (could be enhanced to use actual tool_name)
            inference_result = TaskTypeInferencer.infer(None)

        end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        data = self._get_usage_data(start_date, end_date, tool_name=tool_name, tenant_id=tenant_id)

        total_input = data["overall"]["total_input_tokens"] or 0
        total_output = data["overall"]["total_output_tokens"] or 0
        total_tokens = total_input + total_output
        total_requests = data["overall"]["total_requests"] or 0

        # Calculate efficiency metrics
        avg_tokens_per_request = total_tokens / total_requests if total_requests > 0 else 0
        output_ratio = (total_output / total_tokens * 100) if total_tokens > 0 else 0

        # Model distribution
        model_distribution = {}
        for row in data["by_model"]:
            model = row.get("model") or "unknown"
            tokens = (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
            model_distribution[model] = tokens

        # ===== New fields: efficiency score, cost, waste, recommendations =====

        # Calculate total cost
        total_cost = self._calculate_total_cost(data["by_model"])

        # Average cost per request
        avg_cost_per_request = total_cost / total_requests if total_requests > 0 else 0

        # Efficiency score (with version and task_type)
        efficiency_score = self._calculate_efficiency_score(
            total_tokens,
            total_input,
            total_output,
            total_requests,
            total_cost,
            task_type=inference_result.task_type,
            algorithm_version=resolved_version,
        )

        # Waste percentage (with version and task_type)
        waste_percentage = self._calculate_waste_percentage(
            total_input,
            total_output,
            task_type=inference_result.task_type,
            algorithm_version=resolved_version,
        )

        # Generate recommendations
        recommendation_items = self._generate_recommendations(
            efficiency_score,
            output_ratio,
            avg_cost_per_request,
            avg_tokens_per_request,
            model_distribution,
        )

        # Deprecated: flat string list kept for backward compatibility with older
        # clients. New clients should localize via "recommendation_items".
        recommendations = [self._recommendation_fallback(item) for item in recommendation_items]

        return {
            "period_days": days,
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "avg_tokens_per_request": round(avg_tokens_per_request, 2),
            "output_ratio": round(output_ratio, 2),
            "input_output_ratio": round(total_input / total_output, 2) if total_output > 0 else 0,
            "model_distribution": model_distribution,
            "unique_models": len(model_distribution),
            "unique_tools": len(
                {r.get("tool_name") for r in data["by_model"] if r.get("tool_name")}
            ),
            # ===== New fields (v2.0) =====
            "algorithm_version": resolved_version,
            "applied_task_type": inference_result.task_type.value,
            "inference_confidence": inference_result.confidence,
            "overall_efficiency": round(efficiency_score, 1),
            "avg_cost_per_request": round(avg_cost_per_request, 6),
            "waste_percentage": round(waste_percentage, 1),
            # Structured, language-neutral items for frontend localization
            "recommendation_items": recommendation_items,
            # Deprecated string list (language-neutral English baseline)
            "recommendations": recommendations,
        }

    # English fallback renderers keyed by recommendation type. These mirror the
    # i18n templates on the frontend and exist only to keep the deprecated
    # "recommendations" string list populated and language-neutral.
    _RECOMMENDATION_FALLBACKS = {
        "low_efficiency": "Efficiency score is low ({efficiency_score}); review usage patterns to optimize cost.",
        "low_output_ratio": "Output ratio is low ({output_ratio}%); optimize prompts to reduce input token usage.",
        "high_cost_per_request": "Cost per request is high (${avg_cost_per_request}); consider a more economical model.",
        "high_avg_tokens": "Average tokens per request is high ({avg_tokens_per_request}); split tasks or optimize prompts.",
        "high_model_concentration": "Usage is highly concentrated on {top_model} ({top_share}%); explore other models to reduce risk.",
        "healthy": "Usage patterns are healthy; keep up the good practices.",
    }

    def _recommendation_fallback(self, item: dict[str, Any]) -> str:
        """Render a structured recommendation item into a language-neutral string."""
        template = self._RECOMMENDATION_FALLBACKS.get(item.get("type", ""))
        if template is None:
            return str(item.get("type", ""))
        params = item.get("params", {}) or {}
        try:
            return template.format(**params)
        except (KeyError, IndexError):
            return template
