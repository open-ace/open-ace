"""
Open ACE - Response Time Repository

Repository for response time statistics data access.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from app.repositories.database import Database
from app.utils.cache import cached

logger = logging.getLogger(__name__)


class ResponseTimeRepository:
    """Repository for response time statistics."""

    def __init__(self, db: Database | None = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    @cached(ttl=300, key_prefix="response_time", skip_args=[0])
    def get_response_time_stats(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        """
        Get aggregated response time statistics from pre-aggregated table.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.
            tenant_id: Optional tenant filter (None for admin/global scope).

        Returns:
            Dict with aggregated statistics.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                AVG(avg_ms) as avg_ms,
                MIN(min_ms) as min_ms,
                MAX(max_ms) as max_ms,
                SUM(sample_count) as sample_count,
                SUM(success_count) as success_count,
                SUM(failed_count) as failed_count,
                AVG(tool_call_avg_ms) as tool_call_avg_ms,
                AVG(tool_call_ratio) as tool_call_ratio
            FROM response_time_stats
            {where_clause}
        """

        result = self.db.fetch_one(query, tuple(params))

        if not result or not result.get("sample_count"):
            return {
                "avg_response_time_ms": None,
                "min_response_time_ms": None,
                "max_response_time_ms": None,
                "sample_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "tool_call_avg_ms": None,
                "tool_call_ratio": None,
                "data_available": False,
            }

        return {
            "avg_response_time_ms": int(result.get("avg_ms") or 0),
            "min_response_time_ms": int(result.get("min_ms") or 0),
            "max_response_time_ms": int(result.get("max_ms") or 0),
            "sample_count": int(result.get("sample_count") or 0),
            "success_count": int(result.get("success_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
            "tool_call_avg_ms": (
                int(result.get("tool_call_avg_ms") or 0) if result.get("tool_call_avg_ms") else None
            ),
            "tool_call_ratio": result.get("tool_call_ratio"),
            "data_available": True,
        }

    @cached(ttl=300, key_prefix="response_time", skip_args=[0])
    def get_percentile_stats(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        """
        Get P50 and P95 response time statistics.

        Since percentiles cannot be averaged, we need to calculate them from
        aggregated data or use weighted approximation.

        For simplicity, this returns the average of daily percentiles,
        which is an approximation.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.
            tenant_id: Optional tenant filter (None for admin/global scope).

        Returns:
            Dict with P50 and P95 values.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Get daily percentiles and sample counts for weighted average
        query = f"""
            SELECT
                date,
                p50_ms,
                p95_ms,
                sample_count
            FROM response_time_stats
            {where_clause}
            ORDER BY date
        """

        daily_data = self.db.fetch_all(query, tuple(params))

        if not daily_data:
            return {
                "p50_response_time_ms": None,
                "p95_response_time_ms": None,
                "data_available": False,
            }

        # Calculate weighted average of percentiles
        total_samples = sum(d.get("sample_count", 0) for d in daily_data)

        if total_samples == 0:
            return {
                "p50_response_time_ms": None,
                "p95_response_time_ms": None,
                "data_available": False,
            }

        weighted_p50 = (
            sum((d.get("p50_ms") or 0) * (d.get("sample_count", 0) or 0) for d in daily_data)
            / total_samples
        )

        weighted_p95 = (
            sum((d.get("p95_ms") or 0) * (d.get("sample_count", 0) or 0) for d in daily_data)
            / total_samples
        )

        return {
            "p50_response_time_ms": int(weighted_p50),
            "p95_response_time_ms": int(weighted_p95),
            "data_available": True,
        }

    @cached(ttl=300, key_prefix="response_time", skip_args=[0])
    def get_response_time_trend(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
    ) -> list[dict]:
        """
        Get response time trend by date.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.
            tenant_id: Optional tenant filter (None for admin/global scope).

        Returns:
            List of daily response time statistics.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                date,
                SUM(sample_count) as sample_count,
                AVG(avg_ms) as avg_ms,
                AVG(p50_ms) as p50_ms,
                AVG(p95_ms) as p95_ms,
                SUM(success_count) as success_count,
                SUM(failed_count) as failed_count,
                AVG(tool_call_avg_ms) as tool_call_avg_ms,
                AVG(tool_call_ratio) as tool_call_ratio
            FROM response_time_stats
            {where_clause}
            GROUP BY date
            ORDER BY date
        """

        results = self.db.fetch_all(query, tuple(params))

        trend = []
        for row in results:
            trend.append(
                {
                    "date": row.get("date"),
                    "avg_response_time_ms": int(row.get("avg_ms") or 0),
                    "p50_response_time_ms": int(row.get("p50_ms") or 0),
                    "p95_response_time_ms": int(row.get("p95_ms") or 0),
                    "sample_count": int(row.get("sample_count") or 0),
                    "success_count": int(row.get("success_count") or 0),
                    "failed_count": int(row.get("failed_count") or 0),
                    "tool_call_avg_ms": (
                        int(row.get("tool_call_avg_ms") or 0)
                        if row.get("tool_call_avg_ms")
                        else None
                    ),
                    "tool_call_ratio": row.get("tool_call_ratio"),
                }
            )

        return trend

    def cleanup_old_data(self, days_to_keep: int = 90) -> int:
        """
        Clean up old performance data.

        Args:
            days_to_keep: Number of days to keep.

        Returns:
            Number of rows deleted.
        """
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

        # Delete from request_performance
        query1 = "DELETE FROM request_performance WHERE date(started_at) < ?"
        self.db.execute(query1, (cutoff_date,))

        # Note: response_time_stats is kept longer (365 days) and cleaned separately

        logger.info(f"Cleaned up performance data older than {cutoff_date}")
        return 0  # SQLite doesn't return row count easily

    def cleanup_old_stats(self, days_to_keep: int = 365) -> int:
        """
        Clean up old pre-aggregated statistics.

        Args:
            days_to_keep: Number of days to keep.

        Returns:
            Number of rows deleted.
        """
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

        query = "DELETE FROM response_time_stats WHERE date < ?"
        self.db.execute(query, (cutoff_date,))

        logger.info(f"Cleaned up response time stats older than {cutoff_date}")
        return 0
