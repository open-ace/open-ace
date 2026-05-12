"""
Open ACE - AI Computing Explorer - Usage Service

Business logic for usage data operations.
"""

import logging
from datetime import datetime
from typing import Optional

from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)


class UsageService:
    """Service for usage-related business logic."""

    def __init__(self, usage_repo: Optional[UsageRepository] = None):
        """
        Initialize service.

        Args:
            usage_repo: Optional UsageRepository instance for dependency injection.
        """
        self.usage_repo = usage_repo or UsageRepository()

    @cached(ttl=30, key_prefix="usage", skip_args=[0])
    def get_today_usage(
        self, tool_name: Optional[str] = None, host_name: Optional[str] = None
    ) -> list[dict]:
        """
        Get today's usage data, aggregated from daily_usage table.

        Queries daily_usage directly (few rows) instead of JOIN with
        daily_messages (hundreds of thousands of rows) to avoid
        request_count multiplication and improve performance.

        Args:
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records merged by tool_name.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        conditions = ["date = ?"]
        params: list = [today]
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT
                tool_name,
                SUM(tokens_used) as tokens_used,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(cache_tokens) as cache_tokens,
                SUM(request_count) as request_count
            FROM daily_usage
            WHERE {where_clause}
            GROUP BY tool_name
        """

        rows = self.usage_repo.db.fetch_all(query, tuple(params))

        # Also collect models_used and hosts per tool from daily_usage
        detail_query = f"""
            SELECT tool_name, host_name, models_used
            FROM daily_usage
            WHERE {where_clause}
        """
        detail_rows = self.usage_repo.db.fetch_all(detail_query, tuple(params))

        models_by_tool: dict[str, set] = {}
        hosts_by_tool: dict[str, set] = {}
        for row in detail_rows:
            tool = normalize_tool_name(row["tool_name"])
            if tool not in models_by_tool:
                models_by_tool[tool] = set()
                hosts_by_tool[tool] = set()
            hosts_by_tool[tool].add(row["host_name"] or "unknown")
            if row.get("models_used"):
                try:
                    import json

                    models = (
                        json.loads(row["models_used"])
                        if isinstance(row["models_used"], str)
                        else row["models_used"]
                    )
                    if isinstance(models, list):
                        models_by_tool[tool].update(models)
                except (json.JSONDecodeError, TypeError):
                    pass

        result = []
        for row in rows:
            tool = normalize_tool_name(row["tool_name"])
            result.append(
                {
                    "date": today,
                    "tool_name": tool,
                    "tokens_used": row["tokens_used"] or 0,
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_tokens": row["cache_tokens"] or 0,
                    "request_count": row["request_count"] or 0,
                    "models_used": list(models_by_tool.get(tool, set())) or None,
                    "hosts": list(hosts_by_tool.get(tool, set())),
                }
            )

        return result

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_usage_summary(self, host_name: Optional[str] = None) -> dict[str, dict]:
        """
        Get usage summary for all tools.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict[str, Dict]: Summary data keyed by tool name.
        """
        return self.usage_repo.get_summary_by_tool(host_name)

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_tool_usage(
        self, tool_name: str, days: int = 7, host_name: Optional[str] = None
    ) -> list[dict]:
        """
        Get usage data for a specific tool.

        Args:
            tool_name: Name of the tool.
            days: Number of days to look back.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records.
        """
        return self.usage_repo.get_usage_by_tool(tool_name, days, host_name=host_name)

    def get_date_usage(
        self, date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
    ) -> list[dict]:
        """
        Get usage data for a specific date.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records.
        """
        return self.usage_repo.get_usage_by_date(date, tool_name, host_name)

    def get_range_usage(
        self,
        start_date: str,
        end_date: str,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
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
        return self.usage_repo.get_daily_range(start_date, end_date, tool_name, host_name)

    @cached(ttl=300, key_prefix="usage", skip_args=[0])
    def get_all_tools(self) -> list[str]:
        """
        Get list of all tools.

        Returns:
            List[str]: List of tool names.
        """
        return self.usage_repo.get_all_tools()

    @cached(ttl=300, key_prefix="usage", skip_args=[0])
    def get_all_hosts(self) -> list[str]:
        """
        Get list of all hosts.

        Returns:
            List[str]: List of host names.
        """
        return self.usage_repo.get_all_hosts()

    def save_usage(
        self,
        date: str,
        tool_name: str,
        tokens_used: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_tokens: int = 0,
        request_count: int = 0,
        models_used: Optional[list[str]] = None,
        host_name: str = "localhost",
    ) -> bool:
        """
        Save usage data.

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
        return self.usage_repo.save_usage(
            date=date,
            tool_name=tool_name,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            request_count=request_count,
            models_used=models_used,
            host_name=host_name,
        )

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_trend_data(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> list[dict]:
        """
        Get usage trend data aggregated by date and tool.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of usage records by date and tool.
        """
        return self.usage_repo.get_daily_by_tool(start_date, end_date, host_name)
