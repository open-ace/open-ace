"""
Open ACE - ROI Calculator Module
Calculates Return on Investment for AI usage.
Provides cost analysis, savings estimation, and productivity metrics.
"""

from __future__ import annotations


import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional, cast

from app.repositories.database import Database
from app.utils.cache import cached
from app.utils.tool_names import normalize_tool_name

if TYPE_CHECKING:
    from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# Type definition for assumption source
AssumptionSource = Literal["tenant_config", "environment_vars", "request_params", "defaults"]

# Thread pool for parallel queries
_executor = ThreadPoolExecutor(max_workers=4)


def _normalize_tenant_id(value: object) -> int | None:
    """Normalize a tenant identifier to a positive integer.

    Mirrors ``usage_repo.UsageRepository._normalize_tenant_id``. ``None``/0/blank
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


@dataclass
class ModelPricing:
    """Model pricing configuration."""

    input_price: float  # per 1K tokens
    output_price: float  # per 1K tokens


@dataclass(frozen=True)
class ROIAssumptions:
    """Configurable assumptions used to estimate ROI."""

    hourly_labor_cost: float
    productivity_multiplier: float
    avg_time_saved_per_request: float
    currency: str = "USD"

    DEFAULT_HOURLY_LABOR_COST = 50.0
    DEFAULT_PRODUCTIVITY_MULTIPLIER = 10.0
    DEFAULT_AVG_TIME_SAVED_PER_REQUEST = 5.0
    DEFAULT_CURRENCY = "USD"

    @classmethod
    def _read_float_env(cls, env_name: str, default: float) -> float:
        raw_value = os.environ.get(env_name, "").strip()
        if not raw_value:
            return default
        try:
            parsed = float(raw_value)
        except ValueError:
            logger.warning(
                "Invalid ROI env override %s=%r; using default %s", env_name, raw_value, default
            )
            return default
        return parsed if (math.isfinite(parsed) and parsed > 0) else default

    @classmethod
    def from_env(cls) -> "ROIAssumptions":
        """Build default assumptions from environment overrides."""
        currency = os.environ.get("OPENACE_ROI_CURRENCY", cls.DEFAULT_CURRENCY).strip().upper()
        if not currency:
            currency = cls.DEFAULT_CURRENCY

        return cls(
            hourly_labor_cost=cls._read_float_env(
                "OPENACE_ROI_HOURLY_LABOR_COST", cls.DEFAULT_HOURLY_LABOR_COST
            ),
            productivity_multiplier=cls._read_float_env(
                "OPENACE_ROI_PRODUCTIVITY_MULTIPLIER", cls.DEFAULT_PRODUCTIVITY_MULTIPLIER
            ),
            avg_time_saved_per_request=cls._read_float_env(
                "OPENACE_ROI_AVG_TIME_SAVED_PER_REQUEST",
                cls.DEFAULT_AVG_TIME_SAVED_PER_REQUEST,
            ),
            currency=currency,
        )

    def with_overrides(
        self,
        *,
        hourly_labor_cost: float | None = None,
        productivity_multiplier: float | None = None,
        avg_time_saved_per_request: float | None = None,
        currency: str | None = None,
    ) -> "ROIAssumptions":
        """Return a copy with per-request overrides applied."""
        normalized_currency = self.currency
        if currency is not None:
            normalized_currency = currency.strip().upper() or self.currency

        return ROIAssumptions(
            hourly_labor_cost=hourly_labor_cost or self.hourly_labor_cost,
            productivity_multiplier=productivity_multiplier or self.productivity_multiplier,
            avg_time_saved_per_request=avg_time_saved_per_request
            or self.avg_time_saved_per_request,
            currency=normalized_currency,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hourly_labor_cost": round(self.hourly_labor_cost, 2),
            "productivity_multiplier": round(self.productivity_multiplier, 2),
            "avg_time_saved_per_request": round(self.avg_time_saved_per_request, 2),
            "currency": self.currency,
        }

    @classmethod
    def _get_defaults(cls) -> "ROIAssumptions":
        """Get default assumptions without environment variable overrides."""
        return cls(
            hourly_labor_cost=cls.DEFAULT_HOURLY_LABOR_COST,
            productivity_multiplier=cls.DEFAULT_PRODUCTIVITY_MULTIPLIER,
            avg_time_saved_per_request=cls.DEFAULT_AVG_TIME_SAVED_PER_REQUEST,
            currency=cls.DEFAULT_CURRENCY,
        )

    @classmethod
    def _from_dict_with_defaults(cls, data: dict) -> "ROIAssumptions":
        """Create ROIAssumptions from dict, using defaults for missing fields.

        Args:
            data: Dictionary with assumption values (may be partial).

        Returns:
            ROIAssumptions with defaults filled in for missing fields.
        """
        return cls(
            hourly_labor_cost=data.get("hourly_labor_cost", cls.DEFAULT_HOURLY_LABOR_COST),
            productivity_multiplier=data.get(
                "productivity_multiplier", cls.DEFAULT_PRODUCTIVITY_MULTIPLIER
            ),
            avg_time_saved_per_request=data.get(
                "avg_time_saved_per_request", cls.DEFAULT_AVG_TIME_SAVED_PER_REQUEST
            ),
            currency=data.get("currency", cls.DEFAULT_CURRENCY),
        )

    @classmethod
    def from_tenant_or_env(
        cls, tenant: Optional["Tenant"]
    ) -> tuple["ROIAssumptions", AssumptionSource]:
        """Build ROI assumptions from tenant config or environment variables.

        Priority: tenant config > environment vars > defaults.

        Args:
            tenant: Optional Tenant object with settings.

        Returns:
            tuple: (ROIAssumptions, assumption_source)
        """
        # 1. Check tenant configuration
        if tenant and hasattr(tenant, "settings") and tenant.settings:
            roi_assumptions = getattr(tenant.settings, "roi_assumptions", None)
            if roi_assumptions and isinstance(roi_assumptions, dict) and roi_assumptions:
                logger.info(
                    "Using tenant ROI config for tenant_id=%s",
                    getattr(tenant, "id", "unknown"),
                )
                return cls._from_dict_with_defaults(roi_assumptions), "tenant_config"

        # 2. Check environment variables
        env_assumptions = cls.from_env()
        defaults = cls._get_defaults()
        if env_assumptions != defaults:
            logger.info("Using environment ROI config")
            return env_assumptions, "environment_vars"

        # 3. Use defaults
        logger.info("Using default ROI assumptions")
        return defaults, "defaults"


@dataclass
class ROIMetrics:
    """ROI metrics data structure."""

    period: str
    start_date: str
    end_date: str
    total_cost: float = 0.0
    tokens_used: int = 0
    requests_made: int = 0
    estimated_hours_saved: float = 0.0
    estimated_savings: float = 0.0
    productivity_gain: float = 0.0
    roi_percentage: float = 0.0
    cost_per_request: float = 0.0
    cost_per_token: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    efficiency_score: float = 0.0
    assumptions: ROIAssumptions | None = None
    # P0: Estimation labeling fields
    is_estimated: bool = True
    estimation_type: str = "assumptions_based"
    assumption_source: AssumptionSource = "defaults"
    disclaimer: str = ""

    def __post_init__(self):
        """Set default disclaimer after initialization."""
        if not self.disclaimer:
            self.disclaimer = (
                "ROI metrics are estimates based on configurable assumptions. "
                "This version does not support real-time measurement. "
                "Update tenant ROI assumptions in Settings → Tenant Config."
            )

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_cost": round(self.total_cost, 4),
            "tokens_used": self.tokens_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": round(self.input_cost, 4),
            "output_cost": round(self.output_cost, 4),
            "requests_made": self.requests_made,
            "estimated_hours_saved": round(self.estimated_hours_saved, 2),
            "estimated_savings": round(self.estimated_savings, 2),
            "productivity_gain": round(self.productivity_gain, 2),
            "roi_percentage": round(self.roi_percentage, 2),
            "cost_per_request": round(self.cost_per_request, 6),
            "cost_per_token": round(self.cost_per_token, 8),
            "efficiency_score": round(self.efficiency_score, 1),
            "assumptions": self.assumptions.to_dict() if self.assumptions else None,
            "is_estimated": self.is_estimated,
            "estimation_type": self.estimation_type,
            "assumption_source": self.assumption_source,
            "disclaimer": self.disclaimer,
        }


@dataclass
class CostBreakdown:
    """Cost breakdown by model/tool."""

    tool_name: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "model": self.model,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": round(self.input_cost, 4),
            "output_cost": round(self.output_cost, 4),
            "total_cost": round(self.total_cost, 4),
        }


class ROICalculator:
    """ROI Calculator for AI usage analysis."""

    # Model pricing (per 1K tokens, USD)
    MODEL_PRICING = {
        # Claude models
        "claude-3-opus": ModelPricing(input_price=0.015, output_price=0.075),
        "claude-3-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-haiku": ModelPricing(input_price=0.00025, output_price=0.00125),
        "claude-3-5-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-5-haiku": ModelPricing(input_price=0.001, output_price=0.005),
        # Qwen models
        "qwen-max": ModelPricing(input_price=0.02, output_price=0.06),
        "qwen-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen-turbo": ModelPricing(input_price=0.002, output_price=0.006),
        "qwen3-coder-next": ModelPricing(input_price=0.002, output_price=0.006),
        "qwen3.5-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen3.6-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen3-coder-plus": ModelPricing(input_price=0.002, output_price=0.006),
        # GLM models
        "glm-4": ModelPricing(input_price=0.001, output_price=0.001),
        "glm-4-plus": ModelPricing(input_price=0.005, output_price=0.005),
        "glm-4-flash": ModelPricing(input_price=0.0001, output_price=0.0001),
        "glm-4.7": ModelPricing(input_price=0.001, output_price=0.001),
        "glm-5": ModelPricing(input_price=0.002, output_price=0.002),
        "glm-5.1": ModelPricing(input_price=0.003, output_price=0.003),
        # Mimo models (智谱)
        "mimo-v2-pro": ModelPricing(input_price=0.002, output_price=0.002),
        "mimo-v2.5-pro": ModelPricing(input_price=0.003, output_price=0.003),
        # Other Chinese models
        "MiniMax-M2.5": ModelPricing(input_price=0.002, output_price=0.002),
        "kimi-k2.5": ModelPricing(input_price=0.008, output_price=0.008),
        "coder-model": ModelPricing(input_price=0.002, output_price=0.002),
        # GPT models
        "gpt-4": ModelPricing(input_price=0.03, output_price=0.06),
        "gpt-4-turbo": ModelPricing(input_price=0.01, output_price=0.03),
        "gpt-4o": ModelPricing(input_price=0.005, output_price=0.015),
        "gpt-4o-mini": ModelPricing(input_price=0.00015, output_price=0.0006),
        "gpt-3.5-turbo": ModelPricing(input_price=0.0005, output_price=0.0015),
        # Gemini models
        "gemini-pro": ModelPricing(input_price=0.00025, output_price=0.0005),
        "gemini-1.5-pro": ModelPricing(input_price=0.0035, output_price=0.0105),
        "gemini-1.5-flash": ModelPricing(input_price=0.000075, output_price=0.0003),
        # DeepSeek models
        "deepseek-chat": ModelPricing(input_price=0.00014, output_price=0.00028),
        "deepseek-coder": ModelPricing(input_price=0.00014, output_price=0.00028),
    }

    # Default pricing for unknown models
    DEFAULT_PRICING = ModelPricing(input_price=0.01, output_price=0.03)

    def __init__(
        self,
        db: Database | None = None,
        assumptions: ROIAssumptions | None = None,
        assumption_source: AssumptionSource = "defaults",
    ):
        """
        Initialize ROI Calculator.

        Args:
            db: Optional Database instance.
            assumptions: Optional ROI assumptions override.
            assumption_source: Source of ROI assumptions (for metrics labeling).
        """
        self.db = db or Database()
        self.assumptions = assumptions or ROIAssumptions.from_env()
        self.assumption_source = assumption_source

    def __repr__(self) -> str:
        """Stable cache-key representation that includes ROI assumptions."""
        return f"ROICalculator(assumptions={self.assumptions!r})"

    def _build_metrics(
        self,
        *,
        period: str,
        start_date: str,
        end_date: str,
        total_cost: float,
        tokens_used: int,
        input_tokens: int,
        output_tokens: int,
        input_cost: float,
        output_cost: float,
        requests_made: int,
        assumption_source: AssumptionSource = "defaults",
    ) -> ROIMetrics:
        """Build ROI metrics using the calculator's current assumptions.

        Args:
            period: Period description.
            start_date: Start date string.
            end_date: End date string.
            total_cost: Total cost.
            tokens_used: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            input_cost: Input cost.
            output_cost: Output cost.
            requests_made: Number of requests.
            assumption_source: Source of ROI assumptions.

        Returns:
            ROIMetrics object with calculated values.
        """
        estimated_hours_saved = requests_made * self.assumptions.avg_time_saved_per_request / 60
        estimated_savings = estimated_hours_saved * self.assumptions.hourly_labor_cost

        if total_cost > 0:
            roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
        else:
            roi_percentage = 0.0

        productivity_gain = (self.assumptions.productivity_multiplier - 1) * 100
        cost_per_request = total_cost / requests_made if requests_made > 0 else 0
        cost_per_token = total_cost / tokens_used if tokens_used > 0 else 0
        efficiency_score = self._calculate_efficiency_score(
            tokens_used,
            input_tokens,
            output_tokens,
            requests_made,
            total_cost,
            estimated_savings,
        )

        return ROIMetrics(
            period=period,
            start_date=start_date,
            end_date=end_date,
            total_cost=total_cost,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            requests_made=requests_made,
            estimated_hours_saved=estimated_hours_saved,
            estimated_savings=estimated_savings,
            productivity_gain=productivity_gain,
            roi_percentage=roi_percentage,
            cost_per_request=cost_per_request,
            cost_per_token=cost_per_token,
            efficiency_score=efficiency_score,
            assumptions=self.assumptions,
            assumption_source=assumption_source,
        )

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Get pricing for a model."""
        model_lower = model.lower() if model else ""
        for key, pricing in self.MODEL_PRICING.items():
            if key.lower() in model_lower:
                return pricing
        return self.DEFAULT_PRICING

    def parse_model_name(self, model_raw: Any) -> str:
        r"""
        Parse model name from various formats.

        Supports:
        - Simple string: "claude-3-sonnet"
        - JSON array string: "[\"claude-3-sonnet\"]"
        - Multi-model JSON: "[\"claude-3-sonnet\", \"gpt-4\"]"

        Args:
            model_raw: Raw model field value from database.

        Returns:
            Parsed model name string.
        """
        if model_raw is None:
            return "default"

        if isinstance(model_raw, str):
            # Try to parse as JSON
            try:
                import json

                model_parsed = json.loads(model_raw)
                if isinstance(model_parsed, list):
                    # Return first model from list, or default if empty
                    return str(model_parsed[0]) if model_parsed else "default"
                elif isinstance(model_parsed, str):
                    return model_parsed
                else:
                    return str(model_parsed)
            except (json.JSONDecodeError, TypeError):
                # Not JSON, return as-is
                return model_raw
        else:
            # Non-string value, convert to string
            return str(model_raw)

    def _calculate_efficiency_score(
        self,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        total_cost: float,
        estimated_savings: float,
    ) -> float:
        """
        Calculate efficiency score based on multiple factors.

        Args:
            tokens: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            requests: Number of requests.
            total_cost: Total cost.
            estimated_savings: Estimated savings.

        Returns:
            Efficiency score (0-100).
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

        # Factor 2: Cost-benefit ratio
        if total_cost > 0:
            cost_benefit_ratio = estimated_savings / total_cost
            if cost_benefit_ratio >= 2:
                efficiency_score += 15
            elif cost_benefit_ratio >= 1:
                efficiency_score += 10
            elif cost_benefit_ratio >= 0.5:
                efficiency_score += 5

        # Factor 3: Request efficiency
        if requests > 0:
            avg_tokens_per_request = tokens / requests
            if 500 <= avg_tokens_per_request <= 2000:
                efficiency_score += 5
            elif 200 <= avg_tokens_per_request <= 5000:
                efficiency_score += 3

        return min(efficiency_score, 100.0)

    def calculate_cost(self, input_tokens: int, output_tokens: int, model: Any) -> tuple:
        """
        Calculate cost for token usage.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            model: Model name (can be simple string, JSON array string, etc.).

        Returns:
            Tuple of (input_cost, output_cost, total_cost).
        """
        # Parse model name from various formats
        parsed_model = self.parse_model_name(model)
        pricing = self.get_model_pricing(parsed_model)

        input_cost = (input_tokens / 1000) * pricing.input_price
        output_cost = (output_tokens / 1000) * pricing.output_price
        total_cost = input_cost + output_cost

        return input_cost, output_cost, total_cost

    @cached(ttl=60, key_prefix="roi")
    def calculate_roi(
        self,
        start_date: str,
        end_date: str,
        user_id: int | None = None,
        tool_name: str | None = None,
        tenant_id: int | None = None,
    ) -> ROIMetrics | None:
        """
        Calculate ROI for a period.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            user_id: Optional user ID filter.
            tool_name: Optional tool name filter.
            tenant_id: Optional tenant scope (caller's tenant). Included in the
                cache key so one tenant never reads another's aggregate.

        Returns:
            Optional[ROIMetrics]: ROI metrics, or None if no data found.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)

        # Build query
        query = """
            SELECT
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(input_tokens + output_tokens) as total_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params: list[Any] = [start_date, end_date]

        if normalized_tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(normalized_tenant_id)

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)

        row = self.db.fetch_one(query, params)

        if row is None:
            return None

        # Get model breakdown for cost calculation
        model_query = """
            SELECT tool_name, models_used as model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        model_params: list[Any] = [start_date, end_date]

        if normalized_tenant_id is not None:
            model_query += " AND tenant_id = ?"
            model_params.append(normalized_tenant_id)

        if user_id:
            model_query += " AND user_id = ?"
            model_params.append(user_id)

        if tool_name:
            model_query += " AND tool_name = ?"
            model_params.append(tool_name)

        model_query += " GROUP BY tool_name, models_used"

        model_rows = self.db.fetch_all(model_query, model_params)

        # Calculate costs
        total_cost = 0.0
        total_input_cost = 0.0
        total_output_cost = 0.0

        for model_row in model_rows:
            model_raw = model_row.get("model") or "default"
            # Use parse_model_name for consistent parsing
            model = self.parse_model_name(model_raw)

            input_tokens = model_row.get("input_tokens") or 0
            output_tokens = model_row.get("output_tokens") or 0

            input_cost, output_cost, cost = self.calculate_cost(input_tokens, output_tokens, model)
            total_input_cost += input_cost
            total_output_cost += output_cost
            total_cost += cost

        # Get statistics
        requests = row.get("request_count") or 0
        tokens = row.get("total_tokens") or 0
        input_tokens = row.get("total_input_tokens") or 0
        output_tokens = row.get("total_output_tokens") or 0

        return self._build_metrics(
            period=f"{start_date} to {end_date}",
            start_date=start_date,
            end_date=end_date,
            total_cost=total_cost,
            tokens_used=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=total_input_cost,
            output_cost=total_output_cost,
            requests_made=requests,
            assumption_source=self.assumption_source,
        )

    @cached(ttl=60, key_prefix="roi")
    def get_roi_trend(
        self,
        months: int = 6,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ) -> list[ROIMetrics]:
        """
        Get ROI trend over months.

        Optimized: Uses a single query with monthly grouping instead of
        multiple queries per month.

        Args:
            months: Number of months to analyze.
            user_id: Optional user ID filter.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            List of ROIMetrics.
        """
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        start_date = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        normalized_tenant_id = _normalize_tenant_id(tenant_id)

        # Check database type for SQL syntax
        from app.repositories.database import is_postgresql

        if is_postgresql():
            # PostgreSQL uses to_char, need to cast date column
            month_expr = "to_char(date::date, 'YYYY-MM')"
        else:
            # SQLite uses strftime
            month_expr = "strftime('%Y-%m', date)"

        # Single query to get monthly aggregated data
        query = f"""
            SELECT
                {month_expr} as month,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ?
        """
        params: list[Any] = [start_date]

        if normalized_tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(normalized_tenant_id)

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        query += f" GROUP BY {month_expr} ORDER BY month"

        rows = self.db.fetch_all(query, params)

        # Get model breakdown for cost calculation (single query)
        model_query = f"""
            SELECT
                {month_expr} as month,
                tool_name, models_used as model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ?
        """
        model_params: list[Any] = [start_date]

        if normalized_tenant_id is not None:
            model_query += " AND tenant_id = ?"
            model_params.append(normalized_tenant_id)

        if user_id:
            model_query += " AND user_id = ?"
            model_params.append(user_id)

        model_query += f" GROUP BY {month_expr}, tool_name, models_used"

        model_rows = self.db.fetch_all(model_query, model_params)

        # Group model data by month
        model_data_by_month: dict[str, list[dict]] = {}
        for row in model_rows:
            month = row.get("month")
            if month:
                if month not in model_data_by_month:
                    model_data_by_month[month] = []
                model_data_by_month[month].append(row)

        trends = []
        for row in rows:
            month = row.get("month")
            if not month or len(month) < 7:
                continue  # Skip invalid month data

            requests = row.get("request_count") or 0
            input_tokens = row.get("total_input_tokens") or 0
            output_tokens = row.get("total_output_tokens") or 0
            tokens = input_tokens + output_tokens

            # Calculate costs for this month
            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0

            for model_row in model_data_by_month.get(month, []):
                model = model_row.get("model") or "default"
                m_input_tokens = model_row.get("input_tokens") or 0
                m_output_tokens = model_row.get("output_tokens") or 0

                input_cost, output_cost, cost = self.calculate_cost(
                    m_input_tokens, m_output_tokens, model
                )
                total_input_cost += input_cost
                total_output_cost += output_cost
                total_cost += cost

            # Create period string (first day of month to last day)
            period_start = f"{month}-01"
            # Calculate last day of month
            try:
                year, mon = int(month[:4]), int(month[5:7])
                if mon == 12:
                    next_month = datetime(year + 1, 1, 1)
                else:
                    next_month = datetime(year, mon + 1, 1)
                period_end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                period_end = period_start

            trends.append(
                self._build_metrics(
                    period=f"{period_start} to {period_end}",
                    start_date=period_start,
                    end_date=period_end,
                    total_cost=total_cost,
                    tokens_used=tokens,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    input_cost=total_input_cost,
                    output_cost=total_output_cost,
                    requests_made=requests,
                    assumption_source=self.assumption_source,
                )
            )

        return trends

    @cached(ttl=60, key_prefix="roi")
    def get_roi_by_tool(
        self,
        start_date: str,
        end_date: str,
        tenant_id: int | None = None,
    ) -> dict[str, ROIMetrics]:
        """
        Get ROI breakdown by tool.

        Optimized: Uses a single query with tool grouping instead of
        multiple queries per tool (N+1 problem fixed).

        Args:
            start_date: Start date.
            end_date: End date.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            Dict mapping tool name to ROI metrics.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)
        tenant_clause = " AND tenant_id = ?" if normalized_tenant_id is not None else ""

        # Single query to get aggregated data by tool
        query = f"""
            SELECT
                tool_name,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND tool_name IS NOT NULL{tenant_clause}
            GROUP BY tool_name
        """
        params: list[Any] = [start_date, end_date]
        if normalized_tenant_id is not None:
            params.append(normalized_tenant_id)
        rows = self.db.fetch_all(query, params)

        # Single query to get model breakdown by tool
        model_query = f"""
            SELECT
                tool_name,
                models_used as model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND tool_name IS NOT NULL{tenant_clause}
            GROUP BY tool_name, models_used
        """
        model_params: list[Any] = [start_date, end_date]
        if normalized_tenant_id is not None:
            model_params.append(normalized_tenant_id)
        model_rows = self.db.fetch_all(model_query, model_params)

        # Group model data by tool (normalize tool names)
        model_data_by_tool: dict[str, list[dict]] = {}
        for row in model_rows:
            tool = normalize_tool_name(row.get("tool_name", ""))
            if tool:
                if tool not in model_data_by_tool:
                    model_data_by_tool[tool] = []
                model_data_by_tool[tool].append(row)

        result = {}
        for row in rows:
            tool = normalize_tool_name(row.get("tool_name", ""))
            if not tool:
                continue

            requests = row.get("request_count") or 0
            input_tokens = row.get("total_input_tokens") or 0
            output_tokens = row.get("total_output_tokens") or 0
            tokens = input_tokens + output_tokens

            # Calculate costs for this tool
            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0

            for model_row in model_data_by_tool.get(tool, []):
                model = model_row.get("model") or "default"
                m_input_tokens = model_row.get("input_tokens") or 0
                m_output_tokens = model_row.get("output_tokens") or 0

                input_cost, output_cost, cost = self.calculate_cost(
                    m_input_tokens, m_output_tokens, model
                )
                total_input_cost += input_cost
                total_output_cost += output_cost
                total_cost += cost

            result[tool] = self._build_metrics(
                period=f"{start_date} to {end_date}",
                start_date=start_date,
                end_date=end_date,
                total_cost=total_cost,
                tokens_used=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost=total_input_cost,
                output_cost=total_output_cost,
                requests_made=requests,
                assumption_source=self.assumption_source,
            )

        return result

    @cached(ttl=60, key_prefix="roi")
    def get_roi_by_user(
        self,
        start_date: str,
        end_date: str,
        tenant_id: int | None = None,
    ) -> dict[str, ROIMetrics]:
        """
        Get ROI breakdown by user (via host_name grouping).

        Args:
            start_date: Start date.
            end_date: End date.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            Dict mapping host_name to ROI metrics.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)
        tenant_clause = " AND tenant_id = ?" if normalized_tenant_id is not None else ""

        # Aggregate by host_name since daily_usage lacks user_id
        query = f"""
            SELECT
                host_name,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND host_name IS NOT NULL{tenant_clause}
            GROUP BY host_name
        """
        params: list[Any] = [start_date, end_date]
        if normalized_tenant_id is not None:
            params.append(normalized_tenant_id)
        rows = self.db.fetch_all(query, params)

        # Model breakdown by host_name
        model_query = f"""
            SELECT
                host_name,
                models_used as model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND host_name IS NOT NULL{tenant_clause}
            GROUP BY host_name, models_used
        """
        model_params: list[Any] = [start_date, end_date]
        if normalized_tenant_id is not None:
            model_params.append(normalized_tenant_id)
        model_rows = self.db.fetch_all(model_query, model_params)

        # Group model data by host
        model_data_by_host: dict[str, list[dict]] = {}
        for row in model_rows:
            host = row.get("host_name")
            if host:
                model_data_by_host.setdefault(host, []).append(row)

        result = {}
        for row in rows:
            host_name = row.get("host_name")
            if not host_name:
                continue

            requests = row.get("request_count") or 0
            input_tokens = row.get("total_input_tokens") or 0
            output_tokens = row.get("total_output_tokens") or 0
            tokens = input_tokens + output_tokens

            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0

            for model_row in model_data_by_host.get(host_name, []):
                model = model_row.get("model") or "default"
                m_input_tokens = model_row.get("input_tokens") or 0
                m_output_tokens = model_row.get("output_tokens") or 0

                input_cost, output_cost, cost = self.calculate_cost(
                    m_input_tokens, m_output_tokens, model
                )
                total_input_cost += input_cost
                total_output_cost += output_cost
                total_cost += cost

            result[host_name] = self._build_metrics(
                period=f"{start_date} to {end_date}",
                start_date=start_date,
                end_date=end_date,
                total_cost=total_cost,
                tokens_used=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost=total_input_cost,
                output_cost=total_output_cost,
                requests_made=requests,
                assumption_source=self.assumption_source,
            )

        return result

    @staticmethod
    def _merge_models(model1: str, model2: str) -> str:
        """
        Merge two model JSON array strings, deduplicate and sort.

        Args:
            model1: First model JSON string (e.g., '["glm-5"]').
            model2: Second model JSON string.

        Returns:
            Merged and sorted JSON array string.
        """
        try:
            m1 = json.loads(model1) if model1 and model1 != "unknown" else []
            m2 = json.loads(model2) if model2 and model2 != "unknown" else []
            merged = sorted(set(m1 + m2))
            return json.dumps(merged, ensure_ascii=False) if merged else "unknown"
        except (json.JSONDecodeError, TypeError):
            # Fallback: return non-empty value or "unknown"
            return model1 or model2 or "unknown"

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_cost_breakdown(
        self,
        start_date: str,
        end_date: str,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ) -> list[CostBreakdown]:
        """
        Get detailed cost breakdown.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            List of CostBreakdown objects.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)
        query = """
            SELECT tool_name, models_used as model,
                   COUNT(*) as requests,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params: list[Any] = [start_date, end_date]

        if normalized_tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(normalized_tenant_id)

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " GROUP BY tool_name, models_used ORDER BY SUM(input_tokens + output_tokens) DESC"

        rows = self.db.fetch_all(query, params)

        breakdown = []
        for row in rows:
            model = row.get("model") or "unknown"
            input_tokens = row.get("input_tokens") or 0
            output_tokens = row.get("output_tokens") or 0

            input_cost, output_cost, total_cost = self.calculate_cost(
                input_tokens, output_tokens, model
            )

            breakdown.append(
                CostBreakdown(
                    tool_name=normalize_tool_name(row.get("tool_name") or "unknown"),
                    model=model,
                    requests=row.get("requests") or 0,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=total_cost,
                )
            )

        # Aggregate by tool_name (multiple rows may have same tool_name with different models)
        aggregated: dict[str, CostBreakdown] = {}
        for item in breakdown:
            tool = item.tool_name
            if tool in aggregated:
                agg = aggregated[tool]
                agg.requests += item.requests
                agg.input_tokens += item.input_tokens
                agg.output_tokens += item.output_tokens
                agg.input_cost += item.input_cost
                agg.output_cost += item.output_cost
                agg.total_cost += item.total_cost
                # Merge model lists
                agg.model = self._merge_models(agg.model, item.model)
            else:
                aggregated[tool] = item

        return list(aggregated.values())

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_daily_costs(
        self,
        start_date: str,
        end_date: str,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get daily cost data for charting.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            List of daily cost dictionaries.
        """
        normalized_tenant_id = _normalize_tenant_id(tenant_id)
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

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " GROUP BY date ORDER BY date"

        rows = self.db.fetch_all(query, params)

        daily_costs = []
        for row in rows:
            input_tokens = row.get("input_tokens") or 0
            output_tokens = row.get("output_tokens") or 0

            # Use default pricing for daily aggregation
            input_cost, output_cost, total_cost = self.calculate_cost(
                input_tokens, output_tokens, "default"
            )

            # Normalize date to YYYY-MM-DD. On PostgreSQL the `date` column
            # comes back as a datetime.date object, which Flask's default JSON
            # provider would otherwise serialize as an RFC822 HTTP-date (e.g.
            # "Mon, 01 Jun 2026 00:00:00 GMT") and leak onto the chart axis.
            # On SQLite it is already a clean YYYY-MM-DD string. Mirrors the
            # idiom used by usage_repo.get_request_trend / get_request_trend_by_tool.
            date_val = row.get("date")
            if date_val is None:
                date_str = None
            elif hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val)

            daily_costs.append(
                {
                    "date": date_str,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "input_cost": round(input_cost, 4),
                    "output_cost": round(output_cost, 4),
                    "total_cost": round(total_cost, 4),
                }
            )

        return daily_costs

    @cached(ttl=60, key_prefix="roi")
    def get_summary_stats(
        self,
        start_date: str,
        end_date: str,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Get summary statistics for the period.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.
            tenant_id: Optional tenant scope (caller's tenant).

        Returns:
            Dict with summary statistics.
        """
        roi = self.calculate_roi(start_date, end_date, user_id, tenant_id=tenant_id)
        breakdown = self.get_cost_breakdown(start_date, end_date, user_id, tenant_id=tenant_id)

        # Calculate totals
        total_cost = sum(b.total_cost for b in breakdown)
        total_requests = sum(b.requests for b in breakdown)

        # Find top tools by cost
        top_tools = sorted(breakdown, key=lambda x: x.total_cost, reverse=True)[:5]

        return {
            "roi": roi.to_dict() if roi else None,
            "assumptions": self.assumptions.to_dict(),
            "total_cost": round(total_cost, 4),
            "total_requests": total_requests,
            "top_tools": [t.to_dict() for t in top_tools],
            "breakdown_count": len(breakdown),
        }
