"""
Open ACE - Response Time Aggregator Service

Aggregates raw request performance data into daily statistics.
Runs periodically to populate the response_time_stats table.
"""

import logging
import time
from datetime import datetime, timedelta

from app.repositories.database import Database, is_postgresql
from app.utils.cache import get_cache

logger = logging.getLogger(__name__)


class ResponseTimeAggregator:
    """
    Aggregator for response time statistics.

    Reads raw request_performance data and aggregates into daily statistics
    in the response_time_stats table.
    """

    def __init__(self, db: Database | None = None):
        """
        Initialize aggregator.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def aggregate(self, target_date: str | None = None) -> dict:
        """
        Aggregate performance data for a specific date.

        Args:
            target_date: Date to aggregate (YYYY-MM-DD). Defaults to yesterday.

        Returns:
            Dict with aggregation results.
        """
        if target_date is None:
            # Default to yesterday
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        start_time = time.time()
        logger.info(f"Aggregating response time stats for {target_date}")

        try:
            # Query raw data for the target date
            query = """
                SELECT
                    tool_name,
                    host_name,
                    tenant_id,
                    ttft_ms,
                    tool_call_duration_ms,
                    total_duration_ms,
                    status,
                    sample_type
                FROM request_performance
                WHERE date(started_at) = ?
                  AND ttft_ms IS NOT NULL
                  AND ttft_ms >= 0
            """

            rows = self.db.fetch_all(query, (target_date,))

            if not rows:
                logger.info(f"No performance data found for {target_date}")
                return {
                    "date": target_date,
                    "rows_processed": 0,
                    "groups_created": 0,
                }

            # Group by (tool_name, host_name, tenant_id)
            groups: dict[tuple, list[dict]] = {}
            for row in rows:
                key = (
                    row.get("tool_name", "unknown"),
                    row.get("host_name", "localhost"),
                    row.get("tenant_id", 1),
                )
                if key not in groups:
                    groups[key] = []
                groups[key].append(row)

            # Calculate statistics for each group
            groups_created = 0
            for (tool_name, host_name, tenant_id), group_rows in groups.items():
                stats = self._calculate_group_stats(group_rows)

                # Upsert into response_time_stats
                self._upsert_stats(
                    date=target_date,
                    tool_name=tool_name,
                    host_name=host_name,
                    tenant_id=tenant_id,
                    stats=stats,
                )
                groups_created += 1

            # Invalidate cache for response time data
            # Note: Using clear() is a simple approach; could be optimized with
            # pattern-based deletion if needed
            try:
                cache = get_cache()
                if cache:
                    cache.clear()
            except Exception as cache_err:
                logger.warning(f"Failed to clear cache: {cache_err}")

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"Aggregated {len(rows)} rows into {groups_created} groups for {target_date} "
                f"in {duration_ms}ms"
            )

            return {
                "date": target_date,
                "rows_processed": len(rows),
                "groups_created": groups_created,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"Failed to aggregate response time stats for {target_date}: {e}")
            raise

    def _calculate_group_stats(self, rows: list[dict]) -> dict:
        """Calculate statistics for a group of rows."""
        # Filter successful requests for percentile calculation
        ttft_values = []
        tool_call_durations = []

        for row in rows:
            ttft = row.get("ttft_ms")
            if ttft is not None and ttft >= 0:
                ttft_values.append(ttft)

            tool_duration = row.get("tool_call_duration_ms") or 0
            if tool_duration > 0:
                tool_call_durations.append(tool_duration)

        if not ttft_values:
            return {
                "sample_count": 0,
                "success_count": 0,
                "failed_count": 0,
            }

        # Sort for percentile calculation
        ttft_values.sort()

        # Calculate percentiles
        def percentile(sorted_values: list[int], p: float) -> int:
            """Calculate the p-th percentile of sorted values."""
            if not sorted_values:
                return 0
            index = int(len(sorted_values) * p / 100)
            index = min(index, len(sorted_values) - 1)
            return sorted_values[index]

        stats = {
            "avg_ms": sum(ttft_values) / len(ttft_values),
            "p50_ms": percentile(ttft_values, 50),
            "p95_ms": percentile(ttft_values, 95),
            "min_ms": min(ttft_values),
            "max_ms": max(ttft_values),
            "sample_count": len(rows),
            "success_count": sum(1 for r in rows if r.get("status") == "success"),
            "failed_count": sum(1 for r in rows if r.get("status") != "success"),
        }

        # Tool call statistics
        if tool_call_durations:
            stats["tool_call_avg_ms"] = sum(tool_call_durations) / len(tool_call_durations)
            # Tool call ratio: average tool time / average total time
            if stats["avg_ms"] > 0:
                stats["tool_call_ratio"] = stats["tool_call_avg_ms"] / stats["avg_ms"]
            else:
                stats["tool_call_ratio"] = 0
        else:
            stats["tool_call_avg_ms"] = 0
            stats["tool_call_ratio"] = 0

        return stats

    def _upsert_stats(
        self,
        date: str,
        tool_name: str,
        host_name: str,
        tenant_id: int,
        stats: dict,
    ):
        """Upsert statistics into response_time_stats table."""
        if is_postgresql():
            query = """
                INSERT INTO response_time_stats
                (date, tool_name, host_name, tenant_id, avg_ms, p50_ms, p95_ms,
                 min_ms, max_ms, tool_call_avg_ms, tool_call_ratio,
                 sample_count, success_count, failed_count, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (date, tool_name, host_name, tenant_id) DO UPDATE SET
                    avg_ms = EXCLUDED.avg_ms,
                    p50_ms = EXCLUDED.p50_ms,
                    p95_ms = EXCLUDED.p95_ms,
                    min_ms = EXCLUDED.min_ms,
                    max_ms = EXCLUDED.max_ms,
                    tool_call_avg_ms = EXCLUDED.tool_call_avg_ms,
                    tool_call_ratio = EXCLUDED.tool_call_ratio,
                    sample_count = EXCLUDED.sample_count,
                    success_count = EXCLUDED.success_count,
                    failed_count = EXCLUDED.failed_count,
                    updated_at = NOW()
            """
        else:
            query = """
                INSERT OR REPLACE INTO response_time_stats
                (date, tool_name, host_name, tenant_id, avg_ms, p50_ms, p95_ms,
                 min_ms, max_ms, tool_call_avg_ms, tool_call_ratio,
                 sample_count, success_count, failed_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """

        self.db.execute(
            query,
            (
                date,
                tool_name,
                host_name,
                tenant_id,
                stats.get("avg_ms"),
                stats.get("p50_ms"),
                stats.get("p95_ms"),
                stats.get("min_ms"),
                stats.get("max_ms"),
                stats.get("tool_call_avg_ms"),
                stats.get("tool_call_ratio"),
                stats.get("sample_count", 0),
                stats.get("success_count", 0),
                stats.get("failed_count", 0),
            ),
        )

    def aggregate_range(self, start_date: str, end_date: str) -> dict:
        """
        Aggregate performance data for a date range.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            Dict with aggregation results.
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        results = {
            "dates_processed": 0,
            "total_rows": 0,
            "total_groups": 0,
        }

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            try:
                result = self.aggregate(date_str)
                results["dates_processed"] += 1
                results["total_rows"] += result.get("rows_processed", 0)
                results["total_groups"] += result.get("groups_created", 0)
            except Exception as e:
                logger.error(f"Failed to aggregate {date_str}: {e}")

            current += timedelta(days=1)

        return results

    def backfill_missing_days(self, days_back: int = 7) -> dict:
        """
        Backfill missing days in response_time_stats.

        Args:
            days_back: Number of days to look back.

        Returns:
            Dict with backfill results.
        """
        end_date = datetime.now() - timedelta(days=1)
        start_date = end_date - timedelta(days=days_back)

        return self.aggregate_range(
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
