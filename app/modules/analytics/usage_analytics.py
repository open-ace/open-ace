"""
Open ACE - Usage Analytics Module

Provides comprehensive usage analytics for enterprise insights.
Analyzes trends, detects anomalies, and generates reports.

Issue #3244: Forecast algorithm now uses continuous calendar days,
excludes incomplete current day, and returns history window metadata.
"""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, NamedTuple

from app.repositories.database import Database
from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached
from app.utils.datetime_utils import (
    ForecastWindow,
    generate_date_spine,
    get_business_date,
    get_forecast_window,
)

logger = logging.getLogger(__name__)

# Thread pool for parallel queries
_executor = ThreadPoolExecutor(max_workers=4)

# Issue #3244: Algorithm version for forward compatibility
FORECAST_ALGORITHM_VERSION = "v2"

# Issue #3244: Missing days threshold for forecast quality
MISSING_DAYS_THRESHOLD_DEGRADED = 2  # 2-3 missing days -> degraded
MISSING_DAYS_THRESHOLD_UNAVAILABLE = 4  # 4+ missing days -> unavailable

# Forecast quality constants
FORECAST_WINDOW_DAYS = 7  # Moving average window
FORECAST_DECAY_RATE = 0.02  # Horizon decay rate per day
FORECAST_MIN_SAMPLE_DAYS = 7  # Minimum days for forecast
FORECAST_BACKTEST_DAYS = 7  # Days to use for backtesting


class ContinuousDailyTotals(NamedTuple):
    """Result from _get_continuous_daily_totals for Issue #3244.

    Attributes:
        data: List of (date, tokens, requests) tuples.
        start_date: Actual start date used.
        end_date: Actual end date used.
        total_days: Number of days in the window.
        missing_days: Number of days with no data (filled with zeros).
        first_activity_date: First activity date if found.
    """

    data: list[tuple[str, int, int]]
    start_date: str
    end_date: str
    total_days: int
    missing_days: int
    first_activity_date: str | None


def calculate_moving_average(values: Sequence[int | float], window: int = 7) -> float | None:
    """Calculate moving average for Issue #3244.

    Args:
        values: Sequence of numerical values (int or float).
        window: Window size for averaging.

    Returns:
        Moving average, or None if values length is less than window.

    Examples:
        >>> calculate_moving_average([100, 200, 150, 180, 220, 190, 210], 7)
        178.57...
        >>> calculate_moving_average([100, 200], 7) is None
        True
    """
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


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
    peak_day: str | None = None
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

    # Drop severity thresholds (percentage-based)
    DROP_SEVERITY_HIGH_PCT = 90.0  # Drop >= 90% -> high severity
    DROP_SEVERITY_MEDIUM_PCT = 70.0  # Drop >= 70% -> medium severity

    def __init__(self, db: Database | None = None, usage_repo: UsageRepository | None = None):
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
        tenant_id: int | None = None,
    ) -> UsageReport:
        """
        Generate a comprehensive usage report.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            include_trends: Include trend analysis.
            include_anomalies: Include anomaly detection.
            tenant_id: Tenant ID for data isolation.
                None means global access (platform admin).
                int means tenant-scoped access (tenant admin).

        Issue #3245: Added tenant_id parameter for data isolation.

        Returns:
            UsageReport: Comprehensive usage report.
        """
        # Get raw usage data with tenant isolation
        usage_data = self._get_usage_data(start_date, end_date, tenant_id=tenant_id)

        # Calculate summary statistics
        summary = self._calculate_summary(usage_data, start_date, end_date)

        # Create report
        report = UsageReport(period_start=start_date, period_end=end_date, **summary)

        # Add trends
        if include_trends:
            report.trends = self._analyze_trends(start_date, end_date, tenant_id=tenant_id)

        # Add anomalies
        if include_anomalies:
            report.anomalies = self._detect_anomalies(start_date, end_date, tenant_id=tenant_id)

        # Add breakdowns
        report.breakdown_by_tool = self._get_tool_breakdown(
            start_date, end_date, tenant_id=tenant_id
        )
        report.breakdown_by_host = self._get_host_breakdown(
            start_date, end_date, tenant_id=tenant_id
        )

        return report

    def _get_usage_data(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> list[dict]:
        """Get usage data for date range with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            List of usage records.
        """
        if tenant_id is not None:
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
                WHERE date >= ? AND date <= ? AND tenant_id = ?
                GROUP BY date, tool_name, host_name
                ORDER BY date
            """
            return self.db.fetch_all(query, (start_date, end_date, tenant_id))
        else:
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

    def _calculate_summary(
        self,
        usage_data: list[dict],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Calculate summary statistics from usage data in a single pass.

        Args:
            usage_data: List of usage records from the database.
            start_date: Optional start date (YYYY-MM-DD) for period calculation.
            end_date: Optional end date (YYYY-MM-DD) for period calculation.

        Returns:
            Dictionary with summary statistics including daily averages calculated
            over the full report period (not just active days).
        """
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
                # Normalize date to YYYY-MM-DD string (PostgreSQL returns datetime.date)
                if hasattr(date, "strftime"):
                    date = date.strftime("%Y-%m-%d")
                daily_totals[date] = daily_totals.get(date, 0) + d.get("tokens", 0)

        # Calculate number of days in the report period for daily averages
        # Use the full period (start_date to end_date inclusive) rather than
        # just the active days with data. This ensures missing days are treated
        # as zero usage, giving accurate daily averages.
        if start_date and end_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            num_days = (end - start).days + 1
        else:
            # Fallback to active days if dates not provided
            num_days = len(daily_totals) if daily_totals else 1

        daily_avg_tokens = total_tokens / num_days
        daily_avg_requests = total_requests / num_days

        # Find peak day
        peak_day = None
        peak_tokens = 0
        if daily_totals:
            peak_day = max(daily_totals, key=lambda k: daily_totals.get(k, 0))
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

    def _analyze_trends(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> list[TrendAnalysis]:
        """Analyze usage trends with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            List of trend analysis results.
        """
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

        # Get current and previous period data with tenant isolation
        current_data = self._get_daily_totals(start_date, end_date, tenant_id=tenant_id)
        previous_data = self._get_daily_totals(prev_start_str, prev_end_str, tenant_id=tenant_id)

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

    def _get_daily_totals(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> list[dict]:
        """Get daily totals for a period with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            List of daily total records.
        """
        if tenant_id is not None:
            query = """
                SELECT
                    date,
                    SUM(tokens_used) as tokens,
                    SUM(request_count) as requests
                FROM daily_usage
                WHERE date >= ? AND date <= ? AND tenant_id = ?
                GROUP BY date
                ORDER BY date
            """
            return self.db.fetch_all(query, (start_date, end_date, tenant_id))
        else:
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

    def _get_first_activity_date(self, tenant_id: int | None = None) -> str | None:
        """Get first activity date from daily_stats.

        Issue #3244: Used to bound forecast window for new users.

        Args:
            tenant_id: Optional tenant ID for isolation.

        Returns:
            First activity date (YYYY-MM-DD), or None if no data.
        """
        if tenant_id is not None:
            query = """
                SELECT MIN(date) as first_date
                FROM daily_stats
                WHERE tenant_id = ?
            """
            result = self.db.fetch_one(query, (tenant_id,))
        else:
            query = """
                SELECT MIN(date) as first_date
                FROM daily_stats
            """
            result = self.db.fetch_one(query)

        if result and result.get("first_date"):
            return str(result["first_date"])
        return None

    def _get_continuous_daily_totals(
        self,
        window: ForecastWindow,
        tenant_id: int | None = None,
    ) -> ContinuousDailyTotals | None:
        """Get continuous daily totals with missing days filled as zeros.

        Issue #3244: Ensures the forecast window contains exactly the specified
        number of consecutive calendar days, filling missing days with zeros.

        Args:
            window: ForecastWindow with start_date, end_date, and days.
            tenant_id: Optional tenant ID for isolation.

        Returns:
            ContinuousDailyTotals with data and metadata, or None if database error.
        """
        try:
            # Get first activity date for new user boundary
            first_activity_date = self._get_first_activity_date(tenant_id)

            # Adjust window if first activity date is after window start
            actual_start = window.start_date
            actual_end = window.end_date
            actual_days = window.days

            # Only apply first activity date boundary if it's a valid date string
            if first_activity_date and isinstance(first_activity_date, str):
                try:
                    first_activity_dt = datetime.strptime(first_activity_date, "%Y-%m-%d")
                    start_dt = datetime.strptime(actual_start, "%Y-%m-%d")
                    if first_activity_dt > start_dt:
                        actual_start = first_activity_date
                        # Recalculate actual days
                        end_dt = datetime.strptime(actual_end, "%Y-%m-%d")
                        actual_days = (end_dt - first_activity_dt).days + 1
                except (ValueError, TypeError):
                    # Invalid date format, ignore first activity date
                    pass

            # Query database for existing records
            if tenant_id is not None:
                query = """
                    SELECT
                        date,
                        SUM(total_tokens) as tokens,
                        SUM(message_count) as requests
                    FROM daily_stats
                    WHERE date >= ? AND date <= ? AND tenant_id = ?
                    GROUP BY date
                """
                rows = self.db.fetch_all(query, (actual_start, actual_end, tenant_id))
            else:
                query = """
                    SELECT
                        date,
                        SUM(total_tokens) as tokens,
                        SUM(message_count) as requests
                    FROM daily_stats
                    WHERE date >= ? AND date <= ?
                    GROUP BY date
                """
                rows = self.db.fetch_all(query, (actual_start, actual_end))

            # Build lookup dict from database results
            data_lookup: dict[str, tuple[int, int]] = {}
            for row in rows:
                date = str(row["date"])
                tokens = row.get("tokens", 0) or 0
                requests = row.get("requests", 0) or 0
                data_lookup[date] = (tokens, requests)

            # Generate continuous date spine and fill missing days with zeros
            date_spine = generate_date_spine(actual_start, actual_end)
            result_data: list[tuple[str, int, int]] = []
            missing_days = 0

            for date in date_spine:
                if date in data_lookup:
                    tokens, requests = data_lookup[date]
                    result_data.append((date, tokens, requests))
                else:
                    result_data.append((date, 0, 0))
                    missing_days += 1

            return ContinuousDailyTotals(
                data=result_data,
                start_date=actual_start,
                end_date=actual_end,
                total_days=actual_days,
                missing_days=missing_days,
                first_activity_date=first_activity_date,
            )
        except Exception as e:
            logger.error(f"Failed to get continuous daily totals: {e}")
            return None

    def _detect_anomalies(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> list[Anomaly]:
        """Detect usage anomalies with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            List of detected anomalies.
        """
        anomalies: list[Anomaly] = []

        # Get daily data with tenant isolation
        daily_data = self._get_daily_totals(start_date, end_date, tenant_id=tenant_id)

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
                            date=str(date or ""),
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
                    deviation_pct = (
                        ((mean_tokens - value) / mean_tokens) * 100 if mean_tokens > 0 else 0
                    )

                    # Calculate severity based on z-score (symmetric with spike)
                    z_based_severity = (
                        "high" if abs(z_score) > 3 else "medium" if abs(z_score) > 2 else "low"
                    )

                    # Calculate severity based on drop percentage
                    pct_based_severity = (
                        "high"
                        if deviation_pct >= self.DROP_SEVERITY_HIGH_PCT
                        else "medium" if deviation_pct >= self.DROP_SEVERITY_MEDIUM_PCT else "low"
                    )

                    # Take the more severe level
                    severity_map = {"low": 0, "medium": 1, "high": 2}
                    severity = max(
                        z_based_severity, pct_based_severity, key=lambda s: severity_map[s]
                    )

                    anomalies.append(
                        Anomaly(
                            type="drop",
                            metric="tokens",
                            date=str(date or ""),
                            expected_value=mean_tokens,
                            actual_value=value,
                            deviation_percentage=deviation_pct,
                            severity=severity,
                            description=f"Token usage drop on {date}: {value:,} tokens (expected ~{mean_tokens:,.0f})",
                        )
                    )

        return anomalies

    def _get_tool_breakdown(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> dict[str, dict]:
        """Get usage breakdown by tool with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            Dictionary mapping tool names to usage statistics.
        """
        if tenant_id is not None:
            query = """
                SELECT
                    tool_name,
                    SUM(tokens_used) as tokens,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(request_count) as requests,
                    COUNT(DISTINCT date) as days_active
                FROM daily_usage
                WHERE date >= ? AND date <= ? AND tenant_id = ?
                GROUP BY tool_name
                ORDER BY tokens DESC
            """
            rows = self.db.fetch_all(query, (start_date, end_date, tenant_id))
        else:
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

    def _get_host_breakdown(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> dict[str, dict]:
        """Get usage breakdown by host with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            Dictionary mapping host names to usage statistics.
        """
        if tenant_id is not None:
            query = """
                SELECT
                    host_name,
                    SUM(tokens_used) as tokens,
                    SUM(request_count) as requests,
                    COUNT(DISTINCT date) as days_active
                FROM daily_usage
                WHERE date >= ? AND date <= ? AND tenant_id = ?
                GROUP BY host_name
                ORDER BY tokens DESC
            """
            rows = self.db.fetch_all(query, (start_date, end_date, tenant_id))
        else:
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
    def get_forecast(
        self,
        days: int = 7,
        tenant_id: int | None = None,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Get usage forecast based on continuous calendar days.

        Issue #3244: Uses continuous calendar days algorithm, excludes incomplete
        current day, and returns history_window metadata.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            days: Number of days to forecast (default 7).
            tenant_id: Optional tenant ID for isolation.
            business_date: Optional business date for cache key (YYYY-MM-DD).
                If None, current UTC date is used. This parameter is primarily
                for cache key generation to ensure cross-day cache invalidation.

        Returns:
            Dict with forecast data including history_window metadata and quality assessment.

        Raises:
            ValueError: If days is not in valid range 1-90.
        """
        if not isinstance(days, int) or days < 1 or days > 90:
            raise ValueError(f"days must be an integer between 1 and 90, got {days}")

        # Get business date (current UTC date) if not provided
        # This ensures the cache key includes the date for proper invalidation
        if business_date is None:
            business_date = get_business_date()

        # Calculate forecast window (excludes current incomplete day)
        window = get_forecast_window(business_date, days)

        # Get continuous daily totals with missing days filled as zeros
        continuous_data = self._get_continuous_daily_totals(window, tenant_id)

        # Handle database error
        if continuous_data is None:
            return {
                "forecast_available": False,
                "reason": "Database temporarily unavailable",
                "algorithm_version": FORECAST_ALGORITHM_VERSION,
            }

        # Check for new user: if actual days < requested days
        if continuous_data.total_days < days:
            return {
                "forecast_available": False,
                "reason": "Insufficient historical data (user too new)",
                "algorithm_version": FORECAST_ALGORITHM_VERSION,
                "history_window": {
                    "start_date": continuous_data.start_date,
                    "end_date": continuous_data.end_date,
                    "days": continuous_data.total_days,
                    "missing_days": continuous_data.missing_days,
                    "timezone": "UTC",
                    "first_activity_date": continuous_data.first_activity_date,
                },
            }

        # Check for too many missing days
        if continuous_data.missing_days >= MISSING_DAYS_THRESHOLD_UNAVAILABLE:
            return {
                "forecast_available": False,
                "reason": f"Insufficient data quality ({continuous_data.missing_days} missing days)",
                "algorithm_version": FORECAST_ALGORITHM_VERSION,
                "history_window": {
                    "start_date": continuous_data.start_date,
                    "end_date": continuous_data.end_date,
                    "days": continuous_data.total_days,
                    "missing_days": continuous_data.missing_days,
                    "available_days": continuous_data.total_days - continuous_data.missing_days,
                    "timezone": "UTC",
                    "first_activity_date": continuous_data.first_activity_date,
                },
            }

        # Extract values for moving average calculation
        tokens_list = [item[1] for item in continuous_data.data]
        requests_list = [item[2] for item in continuous_data.data]

        # Calculate moving averages
        avg_tokens = calculate_moving_average(tokens_list, days)
        avg_requests = calculate_moving_average(requests_list, days)

        if avg_tokens is None or avg_requests is None:
            return {
                "forecast_available": False,
                "reason": "Insufficient historical data",
                "algorithm_version": FORECAST_ALGORITHM_VERSION,
                "history_window": {
                    "start_date": continuous_data.start_date,
                    "end_date": continuous_data.end_date,
                    "days": continuous_data.total_days,
                    "missing_days": continuous_data.missing_days,
                    "timezone": "UTC",
                    "first_activity_date": continuous_data.first_activity_date,
                },
            }

        # Determine quality and confidence based on missing days
        if continuous_data.missing_days >= MISSING_DAYS_THRESHOLD_DEGRADED:
            quality = "degraded"
            quality_level = "fair"
            quality_desc = "预测质量一般，历史数据存在缺失"
            confidence = 0.5
        else:
            quality = "normal"
            quality_level = "satisfactory"
            quality_desc = "预测质量良好"
            confidence = 0.7

        # Generate forecast dates
        business_dt = datetime.strptime(business_date, "%Y-%m-%d")
        forecast_dates = [
            (business_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, days + 1)
        ]

        result = {
            "forecast_available": True,
            "method": "moving_average",
            "algorithm_version": FORECAST_ALGORITHM_VERSION,
            "quality": quality,
            "period_days": days,
            "horizon_days": days,
            "daily_forecast": {
                "tokens": round(avg_tokens),
                "requests": round(avg_requests),
            },
            "total_forecast": {
                "tokens": round(avg_tokens * days),
                "requests": round(avg_requests * days),
            },
            "forecast_dates": forecast_dates,
            "confidence": confidence,
            "quality_level": quality_level,
            "quality_description": quality_desc,
            "history_window": {
                "start_date": continuous_data.start_date,
                "end_date": continuous_data.end_date,
                "days": continuous_data.total_days,
                "missing_days": continuous_data.missing_days,
                "timezone": "UTC",
                "first_activity_date": continuous_data.first_activity_date,
            },
        }

        return result

    @cached(ttl=60, key_prefix="analytics", skip_args=[0])
    def get_efficiency_metrics(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> dict[str, Any]:
        """
        Calculate efficiency metrics with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date.
            end_date: End date.
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            Dict with efficiency metrics.
        """
        usage_data = self._get_usage_data(start_date, end_date, tenant_id=tenant_id)

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
