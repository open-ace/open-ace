"""
Open ACE - AI Computing Explorer - Summary Service

Service for managing pre-aggregated usage summary data.
Provides fast dashboard queries by maintaining a summary table.
"""

import logging
from datetime import datetime
from typing import Any, Optional, cast

from app.repositories.database import Database, is_postgresql
from app.repositories.usage_repo import UsageRepository
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)


class SummaryService:
    """Service for managing usage summary data."""

    def __init__(self, db: Optional[Database] = None, usage_repo: Optional[UsageRepository] = None):
        """
        Initialize service.

        Args:
            db: Optional Database instance for dependency injection.
            usage_repo: Optional UsageRepository instance.
        """
        self.db = db or Database()
        self.usage_repo = usage_repo or UsageRepository()

    def refresh_summary(self, host_name: Optional[str] = None) -> bool:
        """
        Refresh summary data from daily_messages table.

        This calculates aggregates and stores them in usage_summary table
        for fast dashboard queries.

        Args:
            host_name: Optional host name to refresh only that host's summary.
                       If None, refreshes all summaries including global.

        Returns:
            bool: True if successful.
        """
        try:
            # Calculate aggregates from daily_messages
            aggregates = self._calculate_aggregates(host_name)

            # Update usage_summary table
            self._update_summary_table(aggregates)

            logger.info(f"Summary refreshed successfully for {host_name or 'all hosts'}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh summary: {e}")
            return False

    def _calculate_aggregates(self, host_name: Optional[str] = None) -> list[dict]:
        """
        Calculate aggregate statistics from daily_messages.

        Args:
            host_name: Optional host filter.

        Returns:
            List[Dict]: List of aggregate records.
        """
        # Build query for aggregates
        if host_name:
            # Calculate for specific host
            query = """
                SELECT
                    tool_name,
                    ? as host_name,
                    COUNT(DISTINCT date) as days_count,
                    SUM(tokens_used) as total_tokens,
                    SUM(tokens_used) / COUNT(DISTINCT date) as avg_tokens,
                    COUNT(*) as total_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    MIN(date) as first_date,
                    MAX(date) as last_date
                FROM daily_messages
                WHERE host_name = ?
                GROUP BY tool_name
                ORDER BY tool_name
            """
            params = (host_name, host_name)
        else:
            # Calculate for all hosts (both per-host and global)
            # Per-host summaries
            query_per_host = """
                SELECT
                    tool_name,
                    host_name,
                    COUNT(DISTINCT date) as days_count,
                    SUM(tokens_used) as total_tokens,
                    SUM(tokens_used) / COUNT(DISTINCT date) as avg_tokens,
                    COUNT(*) as total_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    MIN(date) as first_date,
                    MAX(date) as last_date
                FROM daily_messages
                GROUP BY tool_name, host_name
                ORDER BY tool_name, host_name
            """

            # Global summaries (all hosts combined)
            query_global = """
                SELECT
                    tool_name,
                    NULL as host_name,
                    COUNT(DISTINCT date) as days_count,
                    SUM(tokens_used) as total_tokens,
                    SUM(tokens_used) / COUNT(DISTINCT date) as avg_tokens,
                    COUNT(*) as total_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    MIN(date) as first_date,
                    MAX(date) as last_date
                FROM daily_messages
                GROUP BY tool_name
                ORDER BY tool_name
            """

            # Execute both queries
            per_host_results = self.db.fetch_all(query_per_host, ())
            global_results = self.db.fetch_all(query_global, ())
            return self._merge_aggregates(per_host_results + global_results)

        return self._merge_aggregates(self.db.fetch_all(query, params))

    def _merge_aggregates(self, rows: list[dict]) -> list[dict]:
        merged: dict[tuple, dict] = {}
        for row in rows:
            tool = normalize_tool_name(row["tool_name"])
            host = row.get("host_name")
            key = (tool, host)
            if key in merged:
                existing = merged[key]
                existing["days_count"] = max(existing["days_count"], row["days_count"] or 0)
                existing["total_tokens"] += row["total_tokens"] or 0
                existing["total_requests"] += row["total_requests"] or 0
                existing["total_input_tokens"] += row["total_input_tokens"] or 0
                existing["total_output_tokens"] += row["total_output_tokens"] or 0
                if row["first_date"] and (
                    not existing["first_date"] or row["first_date"] < existing["first_date"]
                ):
                    existing["first_date"] = row["first_date"]
                if row["last_date"] and (
                    not existing["last_date"] or row["last_date"] > existing["last_date"]
                ):
                    existing["last_date"] = row["last_date"]
            else:
                merged[key] = {**row, "tool_name": tool}
        for v in merged.values():
            v["avg_tokens"] = v["total_tokens"] // max(v["days_count"], 1)
        return list(merged.values())

    def _update_summary_table(self, aggregates: list[dict]) -> None:
        """
        Update usage_summary table with calculated aggregates.

        Args:
            aggregates: List of aggregate records.
        """
        now = datetime.utcnow()

        with self.db.connection() as conn:
            cursor = conn.cursor()

            for agg in aggregates:
                tool_name = agg["tool_name"]
                host = agg["host_name"]

                # Prepare values
                days_count = agg["days_count"] or 0
                total_tokens = int(agg["total_tokens"] or 0)
                avg_tokens = int(round(agg["avg_tokens"] or 0))
                total_requests = agg["total_requests"] or 0
                total_input_tokens = int(agg["total_input_tokens"] or 0)
                total_output_tokens = int(agg["total_output_tokens"] or 0)
                first_date = agg["first_date"]
                last_date = agg["last_date"]

                if is_postgresql():
                    # PostgreSQL: use ON CONFLICT
                    cursor.execute(
                        """
                        INSERT INTO usage_summary
                        (tool_name, host_name, days_count, total_tokens, avg_tokens,
                         total_requests, total_input_tokens, total_output_tokens,
                         first_date, last_date, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tool_name, host_name) DO UPDATE SET
                            days_count = EXCLUDED.days_count,
                            total_tokens = EXCLUDED.total_tokens,
                            avg_tokens = EXCLUDED.avg_tokens,
                            total_requests = EXCLUDED.total_requests,
                            total_input_tokens = EXCLUDED.total_input_tokens,
                            total_output_tokens = EXCLUDED.total_output_tokens,
                            first_date = EXCLUDED.first_date,
                            last_date = EXCLUDED.last_date,
                            updated_at = EXCLUDED.updated_at
                    """,
                        (
                            tool_name,
                            host,
                            days_count,
                            total_tokens,
                            avg_tokens,
                            total_requests,
                            total_input_tokens,
                            total_output_tokens,
                            first_date,
                            last_date,
                            now,
                        ),
                    )
                else:
                    # SQLite: use INSERT OR REPLACE
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO usage_summary
                        (tool_name, host_name, days_count, total_tokens, avg_tokens,
                         total_requests, total_input_tokens, total_output_tokens,
                         first_date, last_date, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            tool_name,
                            host,
                            days_count,
                            total_tokens,
                            avg_tokens,
                            total_requests,
                            total_input_tokens,
                            total_output_tokens,
                            first_date,
                            last_date,
                            now,
                        ),
                    )

            conn.commit()

    def get_summary(self, host_name: Optional[str] = None) -> dict[str, dict]:
        """
        Get summary data from usage_summary table.

        Args:
            host_name: Optional host filter.
                       If None, returns global summary (all hosts combined).

        Returns:
            Dict[str, Dict]: Summary data keyed by tool name.
        """
        if host_name:
            # Get summary for specific host
            query = """
                SELECT * FROM usage_summary
                WHERE host_name = ?
                ORDER BY total_tokens DESC
            """
            rows = self.db.fetch_all(query, (host_name,))
        else:
            # Get global summary (host_name IS NULL)
            query = """
                SELECT * FROM usage_summary
                WHERE host_name IS NULL
                ORDER BY total_tokens DESC
            """
            rows = self.db.fetch_all(query, ())
        # Convert to dict keyed by tool_name, normalize and merge
        results: dict[str, dict] = {}
        for row in rows:
            tool = normalize_tool_name(row["tool_name"])
            if tool in results:
                existing = results[tool]
                existing["total_tokens"] += row["total_tokens"] or 0
                existing["total_requests"] += row["total_requests"] or 0
                existing["total_input_tokens"] += row["total_input_tokens"] or 0
                existing["total_output_tokens"] += row["total_output_tokens"] or 0
            else:
                results[tool] = {
                    "days_count": row["days_count"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "avg_tokens": row["avg_tokens"] or 0,
                    "total_requests": row["total_requests"] or 0,
                    "total_input_tokens": row["total_input_tokens"] or 0,
                    "total_output_tokens": row["total_output_tokens"] or 0,
                    "first_date": row["first_date"],
                    "last_date": row["last_date"],
                }

        return results

    def get_all_hosts_summary(self) -> dict[str, dict[str, dict]]:
        """
        Get summary data for all hosts.

        Returns:
            Dict[host_name, Dict[tool_name, Dict]]: Nested summary data.
        """
        query = """
            SELECT * FROM usage_summary
            WHERE host_name IS NOT NULL
            ORDER BY host_name, total_tokens DESC
        """
        rows = self.db.fetch_all(query, ())
        results: dict[str, dict[str, Any]] = {}
        for row in rows:
            host = row["host_name"]
            tool = normalize_tool_name(row["tool_name"])
            if host not in results:
                results[host] = {}
            if tool in results[host]:
                existing = results[host][tool]
                existing["total_tokens"] += row["total_tokens"] or 0
                existing["total_requests"] += row["total_requests"] or 0
                existing["total_input_tokens"] += row["total_input_tokens"] or 0
                existing["total_output_tokens"] += row["total_output_tokens"] or 0
            else:
                results[host][tool] = {
                    "days_count": row["days_count"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "avg_tokens": row["avg_tokens"] or 0,
                    "total_requests": row["total_requests"] or 0,
                    "total_input_tokens": row["total_input_tokens"] or 0,
                    "total_output_tokens": row["total_output_tokens"] or 0,
                    "first_date": row["first_date"],
                    "last_date": row["last_date"],
                }

        return results

    def needs_refresh(self) -> bool:
        """
        Check if summary needs to be refreshed.

        Returns:
            bool: True if summary is empty or stale.
        """
        # Check if summary table has data
        query = "SELECT COUNT(*) as count FROM usage_summary"
        result = self.db.fetch_one(query)
        if result and result["count"] > 0:
            # Check if summary is stale (older than 1 hour)
            query_stale = """
                SELECT MAX(updated_at) as last_update FROM usage_summary
            """
            stale_result = self.db.fetch_one(query_stale)
            if stale_result and stale_result["last_update"]:
                last_update = stale_result["last_update"]
                if isinstance(last_update, str):
                    last_update = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
                age = (datetime.utcnow() - last_update.replace(tzinfo=None)).total_seconds()
                return cast("bool", age > 3600)  # Refresh if older than 1 hour
            return True
        return True  # No data, needs refresh

    def get_all_hosts(self) -> list[str]:
        """
        Get list of all hosts from usage_summary table.

        This is much faster than querying daily_messages directly
        because usage_summary has far fewer rows.

        Returns:
            List[str]: List of unique host names.
        """
        query = """
            SELECT DISTINCT host_name
            FROM usage_summary
            WHERE host_name IS NOT NULL
            ORDER BY host_name
        """
        rows = self.db.fetch_all(query)
        return [row["host_name"] for row in rows]
