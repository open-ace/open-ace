"""
Open ACE - Usage Analytics Module

Provides comprehensive usage analytics for enterprise insights.
Analyzes trends, detects anomalies, and generates reports.

Issue #3244: Forecast algorithm now uses continuous calendar days,
excludes incomplete current day, and returns history window metadata.
"""

import logging
import statistics
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

# Forecast quality constants
FORECAST_WINDOW_DAYS = 7  # Moving average window
FORECAST_DECAY_RATE = 0.02  # Horizon decay rate per day
FORECAST_MIN_SAMPLE_DAYS = 7  # Minimum days for forecast
FORECAST_BACKTEST_DAYS = 7  # Days to use for backtesting

# Issue #3244: Algorithm version for forward compatibility
FORECAST_ALGORITHM_VERSION = "v2"

# Issue #3244: Missing days threshold for forecast quality
MISSING_DAYS_THRESHOLD_DEGRADED = 2  # 2-3 missing days -> degraded
MISSING_DAYS_THRESHOLD_UNAVAILABLE = 4  # 4+ missing days -> unavailable


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
        values: List of numerical values.
        window: Window size for averaging.

    Returns:
        Moving average, or None if values length is less than window.
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

        Issue #3243: Use daily_messages for consistency with historical data API.
        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            List of daily total records.
        """
        # Query daily_messages directly for consistency with historical data API
        if tenant_id is not None:
            query = """
                SELECT
                    date,
                    SUM(tokens_used) as tokens,
                    COUNT(*) as requests
                FROM daily_messages
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
                    COUNT(*) as requests
                FROM daily_messages
                WHERE date >= ? AND date <= ?
                GROUP BY date
                ORDER BY date
            """
            return self.db.fetch_all(query, (start_date, end_date))

    def _get_first_activity_date(self, tenant_id: int | None = None) -> str | None:
        """Get the first activity date for a tenant or globally.

        Issue #3244: Used to bound forecast window start for new users.

        Args:
            tenant_id: Optional tenant ID for isolation.

        Returns:
            First activity date as YYYY-MM-DD string, or None if no data.
        """
        if tenant_id is not None:
            query = """
                SELECT MIN(date) as first_date
                FROM daily_usage
                WHERE tenant_id = ?
            """
            result = self.db.fetch_one(query, (tenant_id,))
        else:
            query = """
                SELECT MIN(date) as first_date
                FROM daily_usage
            """
            result = self.db.fetch_one(query)

        if result and result.get("first_date"):
            return str(result["first_date"])
        return None

    def _get_continuous_daily_totals(
        self,
        window: ForecastWindow,
        tenant_id: int | None = None,
    ) -> ContinuousDailyTotals:
        """Get continuous daily totals with missing days filled as zeros.

        Issue #3244: Ensures the forecast window contains exactly the specified
        number of consecutive calendar days, filling missing days with zeros.

        Args:
            window: ForecastWindow with start_date, end_date, and days.
            tenant_id: Optional tenant ID for isolation.

        Returns:
            ContinuousDailyTotals with data and metadata.
        """
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
        try:
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
                rows = self.db.fetch_all(query, (actual_start, actual_end, tenant_id))
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
                rows = self.db.fetch_all(query, (actual_start, actual_end))
        except Exception:
            # Database error - return degraded result with all days as missing
            return ContinuousDailyTotals(
                data=[],
                start_date=actual_start,
                end_date=actual_end,
                total_days=actual_days,
                missing_days=actual_days,
                first_activity_date=first_activity_date,
            )

        # Build lookup from existing data
        data_by_date = {}
        for row in rows:
            date_str = str(row.get("date", ""))
            if date_str:
                data_by_date[date_str] = (
                    row.get("tokens", 0) or 0,
                    row.get("requests", 0) or 0,
                )

        # Generate continuous date spine
        date_spine = generate_date_spine(actual_start, actual_end)

        # Fill missing days with zeros
        continuous_data: list[tuple[str, int, int]] = []
        missing_count = 0

        for date_str in date_spine:
            if date_str in data_by_date:
                tokens, requests = data_by_date[date_str]
                continuous_data.append((date_str, tokens, requests))
            else:
                continuous_data.append((date_str, 0, 0))
                missing_count += 1

        return ContinuousDailyTotals(
            data=continuous_data,
            start_date=actual_start,
            end_date=actual_end,
            total_days=actual_days,
            missing_days=missing_count,
            first_activity_date=first_activity_date,
        )

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

    def _get_historical_data_for_backtest(
        self, start_date: str, end_date: str, tenant_id: int | None = None
    ) -> tuple[list[dict], int]:
        """
        Get historical data for backtest, excluding today, with optional tenant isolation.

        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            tenant_id: Tenant ID for filtering. None means global (no filter).

        Returns:
            Tuple of (historical_data list, sample_days count).
            Today's data is excluded since it may be incomplete.
        """
        daily_data = self._get_daily_totals(start_date, end_date, tenant_id=tenant_id)

        # Exclude today's data (not yet complete)
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        historical_data = [d for d in daily_data if d.get("date") and d["date"] < today]

        return historical_data, len(historical_data)

    def _count_missing_days(self, daily_data: list[dict], expected_days: int) -> int:
        """
        Count missing days in the historical data.

        Args:
            daily_data: List of daily data records.
            expected_days: Expected number of days in the window.

        Returns:
            Number of missing days.
        """
        if not daily_data:
            return expected_days

        actual_dates = {d.get("date") for d in daily_data if d.get("date")}
        return max(0, expected_days - len(actual_dates))

    def _calculate_backtest_wape(self, historical_data: list[dict]) -> float | None:
        """
        Calculate WAPE (Weighted Absolute Percentage Error) via rolling backtest.

        Uses last 7 days of historical data as test set.
        For each test point, predicts using mean of previous 7 days.

        Args:
            historical_data: Historical daily data (excluding today).

        Returns:
            WAPE value (0.0-1.0) or None if insufficient data or zero actuals.
        """
        if len(historical_data) < FORECAST_MIN_SAMPLE_DAYS + FORECAST_BACKTEST_DAYS:
            # Need at least 14 days: 7 for initial training + 7 for testing
            return None

        tokens = [d.get("tokens", 0) for d in historical_data]

        # Rolling backtest on last 7 days
        abs_errors: list[float] = []
        actuals: list[float] = []

        test_start = len(tokens) - FORECAST_BACKTEST_DAYS

        for i in range(test_start, len(tokens)):
            # Predict using mean of previous 7 days
            train_start = i - FORECAST_WINDOW_DAYS
            if train_start < 0:
                continue

            prediction = sum(tokens[train_start:i]) / FORECAST_WINDOW_DAYS
            actual = tokens[i]

            abs_errors.append(abs(actual - prediction))
            actuals.append(actual)

        if not actuals:
            return None

        total_actual = sum(actuals)
        total_abs_error = sum(abs_errors)

        # Handle division by zero (all actuals are 0)
        if total_actual == 0:
            return None

        return total_abs_error / total_actual

    def _apply_horizon_decay(self, base_wape: float, horizon_days: int) -> float:
        """
        Apply horizon decay to adjust forecast error for longer prediction periods.

        Forecast accuracy decreases as we predict further into the future.
        Decay factor increases WAPE by 2% per day beyond the 7-day window.

        Args:
            base_wape: Base WAPE from backtest.
            horizon_days: Number of days to forecast.

        Returns:
            Horizon-adjusted WAPE.
        """
        if horizon_days <= FORECAST_WINDOW_DAYS:
            return base_wape

        decay_factor = 1 + FORECAST_DECAY_RATE * (horizon_days - FORECAST_WINDOW_DAYS)
        return base_wape * decay_factor

    def _detect_outliers(self, daily_data: list[dict]) -> tuple[int, float]:
        """
        Detect outliers using MAD (Median Absolute Deviation) method.

        An outlier is defined as: value > median + 3 * MAD

        Args:
            daily_data: Historical daily data.

        Returns:
            Tuple of (outlier_count, outlier_ratio).
        """
        values = [d.get("tokens", 0) for d in daily_data]

        if not values:
            return 0, 0.0

        median = statistics.median(values)
        mad = statistics.median([abs(v - median) for v in values])

        # MAD = 0 means all values are identical, no outliers exist
        # This is theoretically correct: no variation = no anomalies
        if mad == 0:
            return 0, 0.0

        outlier_threshold = median + 3 * mad
        outliers = [v for v in values if v > outlier_threshold]

        count = len(outliers)
        ratio = count / len(values)

        return count, ratio

    def _assess_forecast_quality(
        self,
        adjusted_wape: float | None,
        sample_days: int,
        missing_days: int,
        outlier_ratio: float,
    ) -> tuple[str, str, float | None]:
        """
        Assess forecast quality based on multiple metrics.

        Quality levels:
        - quality: adjusted_wape < 10% AND missing <= 1
        - satisfactory: adjusted_wape < 20% AND missing <= 3
        - fair: adjusted_wape < 35% AND missing <= 5
        - poor: adjusted_wape >= 35% OR missing > 5 OR sample < 14
        - unavailable: sample < 7 OR wape is None

        Args:
            adjusted_wape: Horizon-adjusted WAPE (or None if unavailable).
            sample_days: Number of historical sample days.
            missing_days: Number of missing days in data.
            outlier_ratio: Ratio of outlier days.

        Returns:
            Tuple of (quality_level, quality_description, confidence).
        """
        # Unavailable cases
        if sample_days < FORECAST_MIN_SAMPLE_DAYS:
            return (
                "unavailable",
                "样本不足，无法提供质量评估",
                None,
            )

        if adjusted_wape is None:
            return (
                "unavailable",
                "数据无效或全为零，无法计算回测误差",
                None,
            )

        # Determine base quality level
        quality_level: str
        quality_desc: str

        if adjusted_wape < 0.10 and missing_days <= 1:
            quality_level = "quality"
            quality_desc = "数据完整，波动小，预测质量高"
        elif adjusted_wape < 0.20 and missing_days <= 3:
            quality_level = "satisfactory"
            quality_desc = "预测质量良好，可供参考"
        elif adjusted_wape < 0.35 and missing_days <= 5:
            quality_level = "fair"
            quality_desc = "预测质量一般，建议谨慎参考"
        else:
            quality_level = "poor"
            quality_desc = "预测质量较低，仅作趋势参考"

        # Downgrade if too many outliers
        if outlier_ratio > 0.10:
            if quality_level == "quality":
                quality_level = "satisfactory"
                quality_desc = "预测质量良好（检测到异常峰值）"
            elif quality_level == "satisfactory":
                quality_level = "fair"
                quality_desc = "预测质量一般（检测到异常峰值）"
            elif quality_level == "fair":
                quality_level = "poor"
                quality_desc = "预测质量较低（检测到异常峰值）"

        # Map quality level to confidence for backward compatibility
        confidence_mapping = {
            "quality": 0.9,
            "satisfactory": 0.7,
            "fair": 0.5,
            "poor": 0.3,
            "unavailable": None,
        }
        confidence = confidence_mapping.get(quality_level)

        return quality_level, quality_desc, confidence

    @cached(ttl=120, key_prefix="analytics", skip_args=[0])
    def get_forecast(
        self,
        days: int = 7,
        tenant_id: int | None = None,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Get usage forecast based on continuous calendar days.

        Issue #3244: Uses continuous calendar days, excludes incomplete current day,
        and returns history window metadata.
        Issue #3245: Added tenant_id parameter for data isolation.

        Args:
            days: Number of days to forecast (must be 1-90).
            tenant_id: Tenant ID for filtering. None means global (no filter).
            business_date: Optional business date in UTC (YYYY-MM-DD).
                If not provided, uses current UTC date. Included in cache key
                to prevent cross-date cache issues.

        Returns:
            Dict with forecast data including quality metrics and history_window.

        Raises:
            ValueError: If days is not in valid range 1-90.
        """
        if not isinstance(days, int) or days < 1 or days > 90:
            raise ValueError(f"days must be an integer between 1 and 90, got {days}")

        # Get business date (current UTC date if not provided)
        if business_date is None:
            business_date = get_business_date()

        # Get forecast window (excludes current incomplete day)
        window = get_forecast_window(business_date, days)

        # Get continuous daily totals with tenant isolation
        continuous_data = self._get_continuous_daily_totals(window, tenant_id)

        # Calculate actual sample days (days with data, not zeros)
        sample_days = continuous_data.total_days - continuous_data.missing_days

        # Check minimum sample requirement - need at least some real data
        # Even with missing days, we can still provide a forecast (with degraded quality)
        if sample_days == 0:
            return {
                "forecast_available": False,
                "reason": "No historical data available",
                "quality_level": "unavailable",
                "quality_description": "无历史数据，无法提供预测",
                "quality_metrics": {
                    "sample_days": sample_days,
                    "missing_days": continuous_data.missing_days,
                    "window_days": window.days,
                },
                "horizon_days": days,
                "history_window": {
                    "start_date": continuous_data.start_date,
                    "end_date": continuous_data.end_date,
                    "total_days": continuous_data.total_days,
                    "missing_days": continuous_data.missing_days,
                    "first_activity_date": continuous_data.first_activity_date,
                },
                # Backward compatibility
                "confidence": None,
                "_deprecated_note": "confidence 字段将废弃，请迁移至 quality_level 和 quality_metrics",
            }

        # Calculate quality based on missing days ratio
        missing_ratio = continuous_data.missing_days / continuous_data.total_days
        if continuous_data.missing_days >= MISSING_DAYS_THRESHOLD_UNAVAILABLE:
            quality_level = "unavailable"
            quality_desc = "缺失天数过多，无法提供可靠预测"
        elif continuous_data.missing_days >= MISSING_DAYS_THRESHOLD_DEGRADED:
            quality_level = "fair"
            quality_desc = (
                f"数据部分缺失（{continuous_data.missing_days}天无记录），预测精度可能降低"
            )
        else:
            quality_level = "quality"
            quality_desc = "基于连续日历日的移动平均预测"

        # Extract tokens and requests for moving average
        tokens = [d[1] for d in continuous_data.data]
        requests = [d[2] for d in continuous_data.data]

        # Calculate moving average
        avg_tokens = calculate_moving_average(tokens, window.days) or 0
        avg_requests = calculate_moving_average(requests, window.days) or 0

        # Generate forecast dates
        business_dt = datetime.strptime(business_date, "%Y-%m-%d")
        forecast_dates = []
        for i in range(1, days + 1):
            forecast_dates.append((business_dt + timedelta(days=i)).strftime("%Y-%m-%d"))

        result = {
            "forecast_available": True,
            "algorithm_version": FORECAST_ALGORITHM_VERSION,
            "method": "moving_average",
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
            "quality_level": quality_level,
            "quality_description": quality_desc,
            "quality_metrics": {
                "sample_days": sample_days,
                "window_days": window.days,
                "missing_days": continuous_data.missing_days,
                "missing_ratio": round(missing_ratio, 4),
            },
            "history_window": {
                "start_date": continuous_data.start_date,
                "end_date": continuous_data.end_date,
                "total_days": continuous_data.total_days,
                "missing_days": continuous_data.missing_days,
                "first_activity_date": continuous_data.first_activity_date,
            },
            # Backward compatibility (deprecated)
            "confidence": (
                0.9 if quality_level == "quality" else 0.7 if quality_level == "fair" else None
            ),
            "_deprecated_note": "confidence 字段将废弃，请迁移至 quality_level 和 quality_metrics",
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
