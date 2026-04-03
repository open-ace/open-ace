#!/usr/bin/env python3
"""
Open ACE - Usage Repository

Repository for usage data access operations.
"""

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict, List, Optional

from app.repositories.database import Database

logger = logging.getLogger(__name__)


# Cache for JSON parsing to avoid repeated parsing of same strings
@lru_cache(maxsize=256)
def _parse_json_cached(json_str: Optional[str]) -> Optional[List[str]]:
    """
    Parse JSON string with caching for performance.

    Args:
        json_str: JSON string to parse.

    Returns:
        Parsed list or None.
    """
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


class UsageRepository:
    """Repository for usage data operations."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def save_usage(
        self,
        date: str,
        tool_name: str,
        tokens_used: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_tokens: int = 0,
        request_count: int = 0,
        models_used: Optional[List[str]] = None,
        host_name: str = "localhost",
    ) -> bool:
        """
        Save or update usage data for a specific date and tool.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Name of the tool.
            tokens_used: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            cache_tokens: Cache tokens.
            request_count: Number of requests.
            models_used: List of models used.
            host_name: Host name.

        Returns:
            bool: True if successful.
        """
        models_json = json.dumps(models_used) if models_used else None

        with self.db.connection() as conn:
            cursor = conn.cursor()
            # Use different syntax for SQLite and PostgreSQL
            from app.repositories.database import is_postgresql

            if is_postgresql():
                cursor.execute(
                    """
                    INSERT INTO daily_usage
                    (date, tool_name, host_name, tokens_used, input_tokens, output_tokens,
                     cache_tokens, request_count, models_used)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date, tool_name, host_name) DO UPDATE SET
                        tokens_used = EXCLUDED.tokens_used,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        cache_tokens = EXCLUDED.cache_tokens,
                        request_count = EXCLUDED.request_count,
                        models_used = EXCLUDED.models_used
                """,
                    (
                        date,
                        tool_name,
                        host_name,
                        tokens_used,
                        input_tokens,
                        output_tokens,
                        cache_tokens,
                        request_count,
                        models_json,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO daily_usage
                    (date, tool_name, host_name, tokens_used, input_tokens, output_tokens,
                     cache_tokens, request_count, models_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        date,
                        tool_name,
                        host_name,
                        tokens_used,
                        input_tokens,
                        output_tokens,
                        cache_tokens,
                        request_count,
                        models_json,
                    ),
                )
            conn.commit()

        logger.debug(f"Saved usage: {date} - {tool_name} - {host_name}")
        return True

    def get_usage_by_date(
        self, date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get usage data for a specific date.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records.
        """
        conditions = ["date = ?"]
        params = [date]

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT * FROM daily_messages
            WHERE {' AND '.join(conditions)}
            ORDER BY date DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            if row.get("models_used"):
                row["models_used"] = _parse_json_cached(row["models_used"])
            if "request_count" not in row:
                row["request_count"] = 0
            results.append(row)

        return results

    def get_usage_by_tool(
        self,
        tool_name: str,
        days: int = 7,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get usage data for a specific tool over a date range.

        Args:
            tool_name: Name of the tool.
            days: Number of days to look back.
            end_date: Optional end date (defaults to today).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records.
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)).strftime(
            "%Y-%m-%d"
        )

        conditions = ["tool_name = ?", "date >= ?", "date <= ?"]
        params = [tool_name, start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT * FROM daily_messages
            WHERE {' AND '.join(conditions)}
            ORDER BY date DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            if row.get("models_used"):
                row["models_used"] = _parse_json_cached(row["models_used"])
            results.append(row)

        return results

    def get_daily_range(
        self,
        start_date: str,
        end_date: str,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get usage data for a date range.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT * FROM daily_messages
            WHERE {' AND '.join(conditions)}
            ORDER BY date DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            if row.get("models_used"):
                row["models_used"] = _parse_json_cached(row["models_used"])
            results.append(row)

        return results

    def get_summary_by_tool(self, host_name: Optional[str] = None) -> Dict[str, Dict]:
        """
        Get summary statistics for all tools.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict[str, Dict]: Summary data keyed by tool name.
        """
        conditions = []
        params = []

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                tool_name,
                COUNT(DISTINCT date) as days_count,
                SUM(tokens_used) as total_tokens,
                AVG(tokens_used) as avg_tokens,
                COUNT(*) as total_requests,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                MIN(date) as first_date,
                MAX(date) as last_date
            FROM daily_messages
            {where_clause}
            GROUP BY tool_name
            ORDER BY total_tokens DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = {}
        for row in rows:
            results[row["tool_name"]] = {
                "days_count": row["days_count"],
                "total_tokens": row["total_tokens"] or 0,
                "avg_tokens": round(row["avg_tokens"], 2) if row["avg_tokens"] else 0,
                "total_requests": row["total_requests"] if row["total_requests"] else 0,
                "total_input_tokens": row["total_input_tokens"] or 0,
                "total_output_tokens": row["total_output_tokens"] or 0,
                "first_date": row["first_date"],
                "last_date": row["last_date"],
            }

        return results

    def get_all_tools(self) -> List[str]:
        """
        Get list of all tools.

        Returns:
            List[str]: List of tool names.
        """
        query = """
            SELECT DISTINCT tool_name 
            FROM daily_messages 
            ORDER BY tool_name
        """

        rows = self.db.fetch_all(query)
        return [row["tool_name"] for row in rows]

    def get_all_hosts(self) -> List[str]:
        """
        Get list of all hosts.

        Returns:
            List[str]: List of host names.
        """
        query = """
            SELECT DISTINCT host_name 
            FROM daily_messages 
            ORDER BY host_name
        """

        rows = self.db.fetch_all(query)
        return [row["host_name"] for row in rows]

    def get_daily_aggregated(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get usage data aggregated by date for trend charts.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of aggregated usage records by date.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT
                date,
                SUM(tokens_used) as tokens,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                COUNT(*) as requests
            FROM daily_messages
            WHERE {' AND '.join(conditions)}
            GROUP BY date
            ORDER BY date ASC
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Ensure all values are integers
        results = []
        for row in rows:
            results.append(
                {
                    "date": row["date"],
                    "tokens": int(row["tokens"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "requests": int(row["requests"] or 0),
                }
            )

        return results

    def get_daily_by_tool(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get usage data aggregated by date and tool for trend charts.

        Uses pre-aggregated daily_stats table for fast queries.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records by date and tool.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Use pre-aggregated daily_stats table for fast queries
        query = f"""
            SELECT
                date,
                tool_name,
                SUM(total_tokens) as tokens
            FROM daily_stats
            WHERE {' AND '.join(conditions)}
            GROUP BY date, tool_name
            ORDER BY date ASC, tool_name ASC
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Ensure all values are integers
        results = []
        for row in rows:
            results.append(
                {"date": row["date"], "tool": row["tool_name"], "tokens": int(row["tokens"] or 0)}
            )

        return results

    def get_request_count_total(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> int:
        """
        Get total request count from daily_usage table.

        Request count represents the number of API calls to LLM providers,
        which is different from message count (each message in conversation).

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            int: Total request count.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT SUM(request_count) as total_requests
            FROM daily_usage
            WHERE {' AND '.join(conditions)}
        """

        result = self.db.fetch_one(query, tuple(params))
        return int(result.get("total_requests", 0) or 0) if result else 0

    def get_request_trend_data(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get request count trend data aggregated by date.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of request counts by date.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT
                date,
                SUM(request_count) as requests
            FROM daily_usage
            WHERE {' AND '.join(conditions)}
            GROUP BY date
            ORDER BY date ASC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            # Convert date to YYYY-MM-DD format if it's a datetime object
            date_val = row["date"]
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                # Parse HTTP date format if needed
                date_str = str(date_val).split()[0] if ' ' in str(date_val) else str(date_val)
            results.append({
                "date": date_str,
                "requests": int(row["requests"] or 0),
            })

        return results

    def get_request_trend_by_tool(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get request count trend data aggregated by date and tool.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of request counts by date and tool.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT
                date,
                tool_name,
                SUM(request_count) as requests
            FROM daily_usage
            WHERE {' AND '.join(conditions)}
            GROUP BY date, tool_name
            ORDER BY date ASC, tool_name ASC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            # Convert date to YYYY-MM-DD format if it's a datetime object
            date_val = row["date"]
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                # Parse HTTP date format if needed
                date_str = str(date_val).split()[0] if ' ' in str(date_val) else str(date_val)
            results.append({
                "date": date_str,
                "tool": row["tool_name"],
                "requests": int(row["requests"] or 0),
            })

        return results

    def get_today_request_stats(self, host_name: Optional[str] = None) -> Dict:
        """
        Get today's request statistics.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict: Today's request stats with total and by-tool breakdown.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        conditions = ["date = ?"]
        params = [today]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Get total
        total_query = f"""
            SELECT SUM(request_count) as total_requests
            FROM daily_usage
            WHERE {' AND '.join(conditions)}
        """
        total_result = self.db.fetch_one(total_query, tuple(params))
        total_requests = int(total_result.get("total_requests", 0) or 0) if total_result else 0

        # Get by tool
        by_tool_query = f"""
            SELECT
                tool_name,
                SUM(request_count) as requests
            FROM daily_usage
            WHERE {' AND '.join(conditions)}
            GROUP BY tool_name
            ORDER BY requests DESC
        """
        by_tool_rows = self.db.fetch_all(by_tool_query, tuple(params))

        by_tool = {}
        for row in by_tool_rows:
            by_tool[row["tool_name"]] = int(row["requests"] or 0)

        return {
            "date": today,
            "total_requests": total_requests,
            "by_tool": by_tool,
        }

    def get_request_stats_by_user(
        self, date: Optional[str] = None, host_name: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get request statistics grouped by user (sender_name).

        This queries daily_messages table to get request counts per user.
        A request is counted when role = 'assistant' (API response).

        Args:
            date: Optional date filter (YYYY-MM-DD). If None, uses today.
            host_name: Optional host name filter.
            user_name: Optional user name filter (matches sender_name prefix).

        Returns:
            List[Dict]: Request stats by user.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        conditions = ["date = ?", "role = ?"]
        params = [date, "assistant"]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if user_name:
            # sender_name format is: {username}-{hostname}-{tool}
            # Use LIKE to match username prefix
            conditions.append("sender_name LIKE ?")
            params.append(f"{user_name}%")

        query = f"""
            SELECT
                sender_name,
                tool_name,
                COUNT(*) as requests,
                SUM(tokens_used) as tokens
            FROM daily_messages
            WHERE {' AND '.join(conditions)}
            GROUP BY sender_name, tool_name
            ORDER BY requests DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            sender_name = row["sender_name"] or "unknown"
            results.append({
                "user": sender_name,
                "tool": row["tool_name"],
                "requests": int(row["requests"] or 0),
                "tokens": int(row["tokens"] or 0),
            })

        return results

    def get_user_request_trend(
        self,
        user_name: str,
        start_date: str,
        end_date: str,
        host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get request trend for a specific user.

        Args:
            user_name: User name (matches sender_name prefix).
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: Request trend by date for the user.
        """
        # sender_name format is: {username}-{hostname}-{tool}
        # Use LIKE to match username prefix
        conditions = ["date >= ?", "date <= ?", "role = ?", "sender_name LIKE ?"]
        params = [start_date, end_date, "assistant", f"{user_name}%"]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        query = f"""
            SELECT
                date,
                COUNT(*) as requests,
                SUM(tokens_used) as tokens
            FROM daily_messages
            WHERE {' AND '.join(conditions)}
            GROUP BY date
            ORDER BY date ASC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            # Convert date to YYYY-MM-DD format if it's a datetime object
            date_val = row["date"]
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                # Parse HTTP date format if needed
                date_str = str(date_val).split()[0] if ' ' in str(date_val) else str(date_val)
            results.append({
                "date": date_str,
                "requests": int(row["requests"] or 0),
                "tokens": int(row["tokens"] or 0),
            })

        return results

    def get_monthly_request_stats_by_user(
        self, year: int, month: int, host_name: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get monthly request statistics grouped by user.

        Args:
            year: Year.
            month: Month (1-12).
            host_name: Optional host name filter.
            user_name: Optional user name filter (matches sender_name prefix).

        Returns:
            List[Dict]: Monthly request stats by user.
        """
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"

        conditions = ["date >= ?", "date < ?", "role = ?"]
        params = [start_date, end_date, "assistant"]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if user_name:
            # sender_name format is: {username}-{hostname}-{tool}
            # Use LIKE to match username prefix
            conditions.append("sender_name LIKE ?")
            params.append(f"{user_name}%")

        query = f"""
            SELECT
                sender_name,
                COUNT(*) as requests,
                SUM(tokens_used) as tokens
            FROM daily_messages
            WHERE {' AND '.join(conditions)}
            GROUP BY sender_name
            ORDER BY requests DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        results = []
        for row in rows:
            sender_name = row["sender_name"] or "unknown"
            results.append({
                "user": sender_name,
                "requests": int(row["requests"] or 0),
                "tokens": int(row["tokens"] or 0),
            })

        return results
