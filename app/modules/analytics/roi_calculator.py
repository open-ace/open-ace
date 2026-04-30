#!/usr/bin/env python3
"""
Open ACE - ROI Calculator Module

Calculates Return on Investment for AI usage.
Provides cost analysis, savings estimation, and productivity metrics.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.repositories.database import Database
from app.utils.cache import cached

logger = logging.getLogger(__name__)

# Thread pool for parallel queries
_executor = ThreadPoolExecutor(max_workers=4)


@dataclass
class ModelPricing:
    """Model pricing configuration."""

    input_price: float  # per 1K tokens
    output_price: float  # per 1K tokens


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
        "claude-3-opus": ModelPricing(input_price=0.015, output_price=0.075),
        "claude-3-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-haiku": ModelPricing(input_price=0.00025, output_price=0.00125),
        "claude-3-5-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-5-haiku": ModelPricing(input_price=0.001, output_price=0.005),
        "qwen-max": ModelPricing(input_price=0.02, output_price=0.06),
        "qwen-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen-turbo": ModelPricing(input_price=0.002, output_price=0.006),
        "gpt-4": ModelPricing(input_price=0.03, output_price=0.06),
        "gpt-4-turbo": ModelPricing(input_price=0.01, output_price=0.03),
        "gpt-4o": ModelPricing(input_price=0.005, output_price=0.015),
        "gpt-4o-mini": ModelPricing(input_price=0.00015, output_price=0.0006),
        "gpt-3.5-turbo": ModelPricing(input_price=0.0005, output_price=0.0015),
        "gemini-pro": ModelPricing(input_price=0.00025, output_price=0.0005),
        "gemini-1.5-pro": ModelPricing(input_price=0.0035, output_price=0.0105),
        "gemini-1.5-flash": ModelPricing(input_price=0.000075, output_price=0.0003),
    }

    # Default pricing for unknown models
    DEFAULT_PRICING = ModelPricing(input_price=0.01, output_price=0.03)

    # Labor cost assumptions
    HOURLY_LABOR_COST = 50.0  # USD/hour

    # Productivity assumptions
    PRODUCTIVITY_MULTIPLIER = 10.0  # AI is 10x faster
    AVG_TIME_SAVED_PER_REQUEST = 5.0  # minutes

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize ROI Calculator.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Get pricing for a model."""
        model_lower = model.lower() if model else ""
        for key, pricing in self.MODEL_PRICING.items():
            if key.lower() in model_lower:
                return pricing
        return self.DEFAULT_PRICING

    def calculate_cost(self, input_tokens: int, output_tokens: int, model: str) -> tuple:
        """
        Calculate cost for token usage.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            model: Model name.

        Returns:
            Tuple of (input_cost, output_cost, total_cost).
        """
        pricing = self.get_model_pricing(model)

        input_cost = (input_tokens / 1000) * pricing.input_price
        output_cost = (output_tokens / 1000) * pricing.output_price
        total_cost = input_cost + output_cost

        return input_cost, output_cost, total_cost

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def calculate_roi(
        self,
        start_date: str,
        end_date: str,
        user_id: Optional[int] = None,
        tool_name: Optional[str] = None,
    ) -> ROIMetrics:
        """
        Calculate ROI for a period.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            user_id: Optional user ID filter.
            tool_name: Optional tool name filter.

        Returns:
            ROIMetrics: ROI metrics.
        """
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
        params = [start_date, end_date]

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)

        row = self.db.fetch_one(query, params)

        # Get model breakdown for cost calculation
        model_query = """
            SELECT tool_name, models_used as model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        model_params = [start_date, end_date]

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
            model = model_row.get("model") or "default"
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

        # Calculate savings
        estimated_hours_saved = requests * self.AVG_TIME_SAVED_PER_REQUEST / 60
        estimated_savings = estimated_hours_saved * self.HOURLY_LABOR_COST

        # Calculate ROI
        if total_cost > 0:
            roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
        else:
            roi_percentage = 0.0

        # Productivity gain
        productivity_gain = (self.PRODUCTIVITY_MULTIPLIER - 1) * 100

        # Unit costs
        cost_per_request = total_cost / requests if requests > 0 else 0
        cost_per_token = total_cost / tokens if tokens > 0 else 0

        return ROIMetrics(
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
            estimated_hours_saved=estimated_hours_saved,
            estimated_savings=estimated_savings,
            productivity_gain=productivity_gain,
            roi_percentage=roi_percentage,
            cost_per_request=cost_per_request,
            cost_per_token=cost_per_token,
        )

    @cached(ttl=120, key_prefix="roi", skip_args=[0])
    def get_roi_trend(self, months: int = 6, user_id: Optional[int] = None) -> List[ROIMetrics]:
        """
        Get ROI trend over months.

        Optimized: Uses a single query with monthly grouping instead of
        multiple queries per month.

        Args:
            months: Number of months to analyze.
            user_id: Optional user ID filter.

        Returns:
            List of ROIMetrics.
        """
        today = datetime.utcnow()
        start_date = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")

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
            WHERE date >= %s
        """
        params = [start_date]

        if user_id:
            query += " AND user_id = %s"
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
            WHERE date >= %s
        """
        model_params = [start_date]

        if user_id:
            model_query += " AND user_id = %s"
            model_params.append(user_id)

        model_query += f" GROUP BY {month_expr}, tool_name, models_used"

        model_rows = self.db.fetch_all(model_query, model_params)

        # Group model data by month
        model_data_by_month: Dict[str, List[Dict]] = {}
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

            # Calculate savings
            estimated_hours_saved = requests * self.AVG_TIME_SAVED_PER_REQUEST / 60
            estimated_savings = estimated_hours_saved * self.HOURLY_LABOR_COST

            # Calculate ROI
            if total_cost > 0:
                roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
            else:
                roi_percentage = 0.0

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
                ROIMetrics(
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
                    estimated_hours_saved=estimated_hours_saved,
                    estimated_savings=estimated_savings,
                    productivity_gain=(self.PRODUCTIVITY_MULTIPLIER - 1) * 100,
                    roi_percentage=roi_percentage,
                    cost_per_request=total_cost / requests if requests > 0 else 0,
                    cost_per_token=total_cost / tokens if tokens > 0 else 0,
                )
            )

        return trends

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_roi_by_tool(self, start_date: str, end_date: str) -> Dict[str, ROIMetrics]:
        """
        Get ROI breakdown by tool.

        Optimized: Uses a single query with tool grouping instead of
        multiple queries per tool (N+1 problem fixed).

        Args:
            start_date: Start date.
            end_date: End date.

        Returns:
            Dict mapping tool name to ROI metrics.
        """
        # Single query to get aggregated data by tool
        query = """
            SELECT
                tool_name,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND tool_name IS NOT NULL
            GROUP BY tool_name
        """
        rows = self.db.fetch_all(query, (start_date, end_date))

        # Single query to get model breakdown by tool
        model_query = """
            SELECT
                tool_name,
                models_used as model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND tool_name IS NOT NULL
            GROUP BY tool_name, models_used
        """
        model_rows = self.db.fetch_all(model_query, (start_date, end_date))

        # Group model data by tool
        model_data_by_tool: Dict[str, List[Dict]] = {}
        for row in model_rows:
            tool = row.get("tool_name")
            if tool:
                if tool not in model_data_by_tool:
                    model_data_by_tool[tool] = []
                model_data_by_tool[tool].append(row)

        result = {}
        for row in rows:
            tool = row.get("tool_name")
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

            # Calculate savings
            estimated_hours_saved = requests * self.AVG_TIME_SAVED_PER_REQUEST / 60
            estimated_savings = estimated_hours_saved * self.HOURLY_LABOR_COST

            # Calculate ROI
            if total_cost > 0:
                roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
            else:
                roi_percentage = 0.0

            result[tool] = ROIMetrics(
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
                estimated_hours_saved=estimated_hours_saved,
                estimated_savings=estimated_savings,
                productivity_gain=(self.PRODUCTIVITY_MULTIPLIER - 1) * 100,
                roi_percentage=roi_percentage,
                cost_per_request=total_cost / requests if requests > 0 else 0,
                cost_per_token=total_cost / tokens if tokens > 0 else 0,
            )

        return result

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_roi_by_user(self, start_date: str, end_date: str) -> Dict[str, ROIMetrics]:
        """
        Get ROI breakdown by user (via host_name grouping).

        Args:
            start_date: Start date.
            end_date: End date.

        Returns:
            Dict mapping host_name to ROI metrics.
        """
        # Aggregate by host_name since daily_usage lacks user_id
        query = """
            SELECT
                host_name,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND host_name IS NOT NULL
            GROUP BY host_name
        """
        rows = self.db.fetch_all(query, (start_date, end_date))

        # Model breakdown by host_name
        model_query = """
            SELECT
                host_name,
                models_used as model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND host_name IS NOT NULL
            GROUP BY host_name, models_used
        """
        model_rows = self.db.fetch_all(model_query, (start_date, end_date))

        # Group model data by host
        model_data_by_host: Dict[str, List[Dict]] = {}
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

            estimated_hours_saved = requests * self.AVG_TIME_SAVED_PER_REQUEST / 60
            estimated_savings = estimated_hours_saved * self.HOURLY_LABOR_COST

            if total_cost > 0:
                roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
            else:
                roi_percentage = 0.0

            result[host_name] = ROIMetrics(
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
                estimated_hours_saved=estimated_hours_saved,
                estimated_savings=estimated_savings,
                productivity_gain=(self.PRODUCTIVITY_MULTIPLIER - 1) * 100,
                roi_percentage=roi_percentage,
                cost_per_request=total_cost / requests if requests > 0 else 0,
                cost_per_token=total_cost / tokens if tokens > 0 else 0,
            )

        return result

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_cost_breakdown(
        self, start_date: str, end_date: str, user_id: Optional[int] = None
    ) -> List[CostBreakdown]:
        """
        Get detailed cost breakdown.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.

        Returns:
            List of CostBreakdown objects.
        """
        query = """
            SELECT tool_name, models_used as model,
                   COUNT(*) as requests,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params = [start_date, end_date]

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
                    tool_name=row.get("tool_name") or "unknown",
                    model=model,
                    requests=row.get("requests") or 0,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=total_cost,
                )
            )

        return breakdown

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_daily_costs(
        self, start_date: str, end_date: str, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get daily cost data for charting.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.

        Returns:
            List of daily cost dictionaries.
        """
        query = """
            SELECT date,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        """
        params = [start_date, end_date]

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

            daily_costs.append(
                {
                    "date": row.get("date"),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "input_cost": round(input_cost, 4),
                    "output_cost": round(output_cost, 4),
                    "total_cost": round(total_cost, 4),
                }
            )

        return daily_costs

    @cached(ttl=60, key_prefix="roi", skip_args=[0])
    def get_summary_stats(
        self, start_date: str, end_date: str, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get summary statistics for the period.

        Args:
            start_date: Start date.
            end_date: End date.
            user_id: Optional user ID filter.

        Returns:
            Dict with summary statistics.
        """
        roi = self.calculate_roi(start_date, end_date, user_id)
        breakdown = self.get_cost_breakdown(start_date, end_date, user_id)

        # Calculate totals
        total_cost = sum(b.total_cost for b in breakdown)
        total_requests = sum(b.requests for b in breakdown)

        # Find top tools by cost
        top_tools = sorted(breakdown, key=lambda x: x.total_cost, reverse=True)[:5]

        return {
            "roi": roi.to_dict(),
            "total_cost": round(total_cost, 4),
            "total_requests": total_requests,
            "top_tools": [t.to_dict() for t in top_tools],
            "breakdown_count": len(breakdown),
        }
