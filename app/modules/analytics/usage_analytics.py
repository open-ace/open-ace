"""
Open ACE - Usage Analytics Module

Provides comprehensive usage analytics for enterprise insights.
Analyzes trends, detects anomalies, and generates reports.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from app.repositories.database import Database
from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached

logger = logging.getLogger(__name__)

# Thread pool for parallel queries
_executor = ThreadPoolExecutor(max_workers=4)


class TrendDirection(Enum):
    """Trend direction for analytics."""

    UP = "up"
    DOWN = "down"
    STABLE = "stable"


class AnomalyType(Enum):
    """Types of usage anomalies."""

    SPIKE = "spike"
    DROP = "drop"
    UNUSUAL_PATTERN = "unusual_pattern"


@dataclass
class TrendAnalysis:
    """Trend analysis result."""

    metric: str
    direction: str
    change_percentage: float
    current_value: float
    previous_value: float
    period_days: int
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "direction": self.direction,
            "change_percentage": round(self.change_percentage, 2),
            "current_value": self.current_value,
            "previous_value": self.previous_value,
            "period_days": self.period_days,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class Anomaly:
    """Detected anomaly."""

    type: str
    metric: str
    date: str
    expected_value: float
    actual_value: float
    deviation_percentage: float
    severity: str  # low, medium, high
    description: str

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "metric": self.metric,
            "date": self.date,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "deviation_percentage": round(self.deviation_percentage, 2),
            "severity": self.severity,
            "description": self.description,
        }


@dataclass
class UsageReport:
    """Comprehensive usage report."""

    period_start: str
    period_end: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    unique_tools: int = 0
    unique_hosts: int = 0
    daily_average_tokens: float = 0.0
    daily_average_requests: float = 0.0
    peak_day: Optional[str] = None
    peak_tokens: int = 0
    trends: list[TrendAnalysis] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    breakdown_by_tool: dict[str, dict] = field(default_factory=dict)
    breakdown_by_host: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "summary": {
                "total_tokens": self.total_tokens,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_requests": self.total_requests,
                "unique_tools": self.unique_tools,
                "unique_hosts": self.unique_hosts,
                "daily_average_tokens": round(self.daily_average_tokens, 2),
                "daily_average_requests": round(self.daily_average_requests, 2),
                "peak_day": self.peak_day,
                "peak_tokens": self.peak_tokens,
            },
            "trends": [t.to_dict() for t in self.trends],
            "anomalies": [a.to_dict() for a in self.anomalies],
            "breakdown_by_tool": self.breakdown_by_tool,
            "breakdown_by_host": self.breakdown_by_host,
        }


class UsageAnalytics:
    """
    Usage analytics service for enterprise insights.

    Features:
    - Trend analysis
    - Anomaly detection
    - Usage forecasting
    - Comprehensive reporting
    """

    # Anomaly detection thresholds
    SPIKE_THRESHOLD = 2.0  # Standard deviations
    DROP_THRESHOLD = 0.5  # Ratio of expected

    def __init__(self, db: Optional[Database] = None, usage_repo: Optional[UsageRepository] = None):
        """
        Initialize analytics service.

        Args:
            db: Optional Database instance.
            usage_repo: Optional UsageRepository instance.
        """
        self.db = db or Database()
        self.usage_repo = usage_repo or UsageRepository()

    @cached(ttl=60, key_prefix="analytics", skip_args=[0])
    def generate_report(
        self,
        start_date: str,
        end_date: str,
        include_trends: bool = True,
        include_anomalies: bool = True,
    ) -> UsageReport:
        """
        Generate a comprehensive usage report.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            include_trends: Include trend analysis.
            include_anomalies: Include anomaly detection.

        Returns:
            UsageReport: Comprehensive usage report.
        """
        # Get raw usage data
        usage_data = self._get_usage_data(start_date, end_date)

        # Calculate summary statistics
        summary = self._calculate_summary(usage_data)

        # Create report
        report = UsageReport(period_start=start_date, period_end=end_date, **summary)

        # Add trends
        if include_trends:
            report.trends = self._analyze_trends(start_date, end_date)

        # Add anomalies
        if include_anomalies:
            report.anomalies = self._detect_anomalies(start_date, end_date)

        # Add breakdowns
        report.breakdown_by_tool = self._get_tool_breakdown(start_date, end_date)
        report.breakdown_by_host = self._get_host_breakdown(start_date, end_date)

        return report

    def _get_usage_data(self, start_date: str, end_date: str) -> list[dict]:
        """Get usage data for date range."""
        query = """
            SELECT
                date,
                tool_name,
                host_name,
                SUM(tokens_used) as tokens,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(request_count) as requests
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY date, tool_name, host_name
            ORDER BY date
        """
        return self.db.fetch_all(query, (start_date, end_date))

    def _calculate_summary(self, usage_data: list[dict]) -> dict[str, Any]:
        """Calculate summary statistics from usage data in a single pass."""
        if not usage_data:
            return {
                "total_tokens": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_requests": 0,
                "unique_tools": 0,
                "unique_hosts": 0,
                "daily_average_tokens": 0.0,
                "daily_average_requests": 0.0,
                "peak_day": None,
                "peak_tokens": 0,
            }

        # Single pass calculation for all totals
        total_tokens = 0
        total_input = 0
        total_output = 0
        total_requests = 0
        tools = set()
        hosts = set()
        daily_totals: dict[str, int] = {}

        for d in usage_data:
            # Accumulate totals
            total_tokens += d.get("tokens", 0)
            total_input += d.get("input_tokens", 0)
            total_output += d.get("output_tokens", 0)
            total_requests += d.get("requests", 0)

            # Collect unique tools and hosts
            if d.get("tool_name"):
                tools.add(d["tool_name"])
            if d.get("host_name"):
                hosts.add(d["host_name"])

            # Aggregate by date
            date = d.get("date")
            if date:
                daily_totals[date] = daily_totals.get(date, 0) + d.get("tokens", 0)

        # Calculate averages
        num_days = len(daily_totals) if daily_totals else 1
        daily_avg_tokens = total_tokens / num_days
        daily_avg_requests = total_requests / num_days

        # Find peak day
        peak_day = None
        peak_tokens = 0
        if daily_totals:
            peak_day = max(daily_totals, key=daily_totals.get)
            peak_tokens = daily_totals[peak_day]

        return {
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_requests": total_requests,
            "unique_tools": len(tools),
            "unique_hosts": len(hosts),
            "daily_average_tokens": daily_avg_tokens,
            "daily_average_requests": daily_avg_requests,
            "peak_day": peak_day,
            "peak_tokens": peak_tokens,
        }

    def _analyze_trends(self, start_date: str, end_date: str) -> list[TrendAnalysis]:
        """Analyze usage trends."""
        trends = []

        # Calculate period length
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        period_days = (end - start).days + 1

        # Compare with previous period
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        prev_start_str = prev_start.strftime("%Y-%m-%d")
        prev_end_str = prev_end.strftime("%Y-%m-%d")

        # Get current and previous period data
        current_data = self._get_daily_totals(start_date, end_date)
        previous_data = self._get_daily_totals(prev_start_str, prev_end_str)

        # Analyze token trend
        current_tokens = sum(d.get("tokens", 0) for d in current_data)
        previous_tokens = sum(d.get("tokens", 0) for d in previous_data)

        if previous_tokens > 0:
            change_pct = ((current_tokens - previous_tokens) / previous_tokens) * 100
            direction = "up" if change_pct > 5 else "down" if change_pct < -5 else "stable"

            trends.append(
                TrendAnalysis(
                    metric="tokens",
                    direction=direction,
                    change_percentage=change_pct,
                    current_value=current_tokens,
                    previous_value=previous_tokens,
                    period_days=period_days,
                    confidence=0.8 if abs(change_pct) > 20 else 0.6,
                )
            )

        # Analyze request trend
        current_requests = sum(d.get("requests", 0) for d in current_data)
        previous_requests = sum(d.get("requests", 0) for d in previous_data)

        if previous_requests > 0:
            change_pct = ((current_requests - previous_requests) / previous_requests) * 100
            direction = "up" if change_pct > 5 else "down" if change_pct < -5 else "stable"

            trends.append(
                TrendAnalysis(
                    metric="requests",
                    direction=direction,
                    change_percentage=change_pct,
                    current_value=current_requests,
                    previous_value=previous_requests,
                    period_days=period_days,
                    confidence=0.8 if abs(change_pct) > 20 else 0.6,
                )
            )

        return trends

    def _get_daily_totals(self, start_date: str, end_date: str) -> list[dict]:
        """Get daily totals for a period."""
        query = """
            SELECT
                date,
                SUM(tokens_used) as tokens,
                SUM(request_count) as requests
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY date
            ORDER BY date
        """
        return self.db.fetch_all(query, (start_date, end_date))

    def _detect_anomalies(self, start_date: str, end_date: str) -> list[Anomaly]:
        """Detect usage anomalies."""
        anomalies: list[Anomaly] = []

        # Get daily data
        daily_data = self._get_daily_totals(start_date, end_date)

        if len(daily_data) < 7:
            return anomalies

        # Calculate mean and std for tokens
        tokens = [d.get("tokens", 0) for d in daily_data]
        mean_tokens = sum(tokens) / len(tokens)
        std_tokens = (sum((t - mean_tokens) ** 2 for t in tokens) / len(tokens)) ** 0.5

        # Detect spikes and drops
        for d in daily_data:
            date = d.get("date")
            value = d.get("tokens", 0)

            if std_tokens > 0:
                z_score = (value - mean_tokens) / std_tokens

                # Spike detection
                if z_score > self.SPIKE_THRESHOLD:
                    anomalies.append(
                        Anomaly(
                            type="spike",
                            metric="tokens",
                            date=date,
                            expected_value=mean_tokens,
                            actual_value=value,
                            deviation_percentage=(
                                ((value - mean_tokens) / mean_tokens) * 100
                                if mean_tokens > 0
                                else 0
                            ),
                            severity="high" if z_score > 3 else "medium",
                            description=f"Token usage spike on {date}: {value:,} tokens (expected ~{mean_tokens:,.0f})",
                        )
                    )

                # Drop detection
                elif value < mean_tokens * self.DROP_THRESHOLD:
                    anomalies.append(
                        Anomaly(
                            type="drop",
                            metric="tokens",
                            date=date,
                            expected_value=mean_tokens,
                            actual_value=value,
                            deviation_percentage=(
                                ((mean_tokens - value) / mean_tokens) * 100
                                if mean_tokens > 0
                                else 0
                            ),
                            severity="low",
                            description=f"Token usage drop on {date}: {value:,} tokens (expected ~{mean_tokens:,.0f})",
                        )
                    )

        return anomalies

    def _get_tool_breakdown(self, start_date: str, end_date: str) -> dict[str, dict]:
        """Get usage breakdown by tool."""
        query = """
            SELECT
                tool_name,
                SUM(tokens_used) as tokens,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(request_count) as requests,
                COUNT(DISTINCT date) as days_active
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY tool_name
            ORDER BY tokens DESC
        """
        rows = self.db.fetch_all(query, (start_date, end_date))

        return {
            row["tool_name"]: {
                "tokens": row["tokens"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "requests": row["requests"],
                "days_active": row["days_active"],
            }
            for row in rows
            if row.get("tool_name")
        }

    def _get_host_breakdown(self, start_date: str, end_date: str) -> dict[str, dict]:
        """Get usage breakdown by host."""
        query = """
            SELECT
                host_name,
                SUM(tokens_used) as tokens,
                SUM(request_count) as requests,
                COUNT(DISTINCT date) as days_active
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY host_name
            ORDER BY tokens DESC
        """
        rows = self.db.fetch_all(query, (start_date, end_date))

        return {
            row["host_name"]: {
                "tokens": row["tokens"],
                "requests": row["requests"],
                "days_active": row["days_active"],
            }
            for row in rows
            if row.get("host_name")
        }

    @cached(ttl=120, key_prefix="analytics", skip_args=[0])
    def get_forecast(self, days: int = 7) -> dict[str, Any]:
        """
        Get usage forecast based on historical data.

        Args:
            days: Number of days to forecast.

        Returns:
            Dict with forecast data.
        """
        # Get last 30 days of data
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        daily_data = self._get_daily_totals(start_date, end_date)

        if len(daily_data) < 7:
            return {
                "forecast_available": False,
                "reason": "Insufficient historical data",
            }

        # Simple moving average forecast
        tokens = [d.get("tokens", 0) for d in daily_data[-7:]]
        requests = [d.get("requests", 0) for d in daily_data[-7:]]

        avg_tokens = sum(tokens) / len(tokens)
        avg_requests = sum(requests) / len(requests)

        # Generate forecast dates
        forecast_dates = []
        for i in range(1, days + 1):
            forecast_dates.append((datetime.utcnow() + timedelta(days=i)).strftime("%Y-%m-%d"))

        return {
            "forecast_available": True,
            "method": "moving_average",
            "period_days": days,
            "daily_forecast": {
                "tokens": round(avg_tokens),
                "requests": round(avg_requests),
            },
            "total_forecast": {
                "tokens": round(avg_tokens * days),
                "requests": round(avg_requests * days),
            },
            "forecast_dates": forecast_dates,
            "confidence": 0.7,  # Simple forecast confidence
        }

    @cached(ttl=60, key_prefix="analytics", skip_args=[0])
    def get_efficiency_metrics(self, start_date: str, end_date: str) -> dict[str, Any]:
        """
        Calculate efficiency metrics.

        Args:
            start_date: Start date.
            end_date: End date.

        Returns:
            Dict with efficiency metrics.
        """
        usage_data = self._get_usage_data(start_date, end_date)

        if not usage_data:
            return {"efficiency_available": False}

        # Calculate metrics
        total_tokens = sum(d.get("tokens", 0) for d in usage_data)
        total_input = sum(d.get("input_tokens", 0) for d in usage_data)
        total_output = sum(d.get("output_tokens", 0) for d in usage_data)
        total_requests = sum(d.get("requests", 0) for d in usage_data)

        # Efficiency ratios
        output_ratio = (total_output / total_tokens * 100) if total_tokens > 0 else 0
        tokens_per_request = total_tokens / total_requests if total_requests > 0 else 0
        output_per_request = total_output / total_requests if total_requests > 0 else 0

        return {
            "efficiency_available": True,
            "output_ratio": round(output_ratio, 2),
            "tokens_per_request": round(tokens_per_request, 2),
            "output_per_request": round(output_per_request, 2),
            "input_output_ratio": round(total_input / total_output, 2) if total_output > 0 else 0,
            "summary": {
                "total_tokens": total_tokens,
                "total_input": total_input,
                "total_output": total_output,
                "total_requests": total_requests,
            },
        }
