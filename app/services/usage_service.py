"""
Open ACE - AI Computing Explorer - Usage Service

Business logic for usage data operations.
"""

import logging
from datetime import date, datetime

from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)


class UsageService:
    """Service for usage-related business logic."""

    def __init__(self, usage_repo: UsageRepository | None = None):
        """
        Initialize service.

        Args:
            usage_repo: Optional UsageRepository instance for dependency injection.
        """
        self.usage_repo = usage_repo or UsageRepository()

    @cached(ttl=30, key_prefix="usage", skip_args=[0])
    def get_today_usage(
        self,
        tool_name: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
    ) -> list[dict]:
        """Get today's usage data, aggregated from agent_sessions table.

        Issue #2842: Query agent_sessions directly for real-time usage data,
        avoiding sync delay from daily_usage table.

        Args:
            tool_name: Optional tool name filter (not used, for API compatibility).
            host_name: Optional host name filter (not used, for API compatibility).
            tenant_id: Optional tenant ID filter.

        Returns:
            List[Dict]: List of usage records by tool_name.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Query agent_sessions for today's sessions
        # This gives real-time data without sync delay
        rows = self.usage_repo.get_today_session_usage(today, tenant_id)

        # Aggregate by normalized tool name
        merged: dict[str, dict] = {}
        for row in rows:
            raw_tool = row.get("tool_name") or "unknown"
            tool = normalize_tool_name(raw_tool)

            if tool not in merged:
                merged[tool] = {
                    "date": today,
                    "tool_name": tool,
                    "tokens_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_tokens": 0,
                    "request_count": 0,
                    "hosts": [],
                }

            merged[tool]["tokens_used"] += row.get("tokens_used") or 0
            merged[tool]["input_tokens"] += row.get("input_tokens") or 0
            merged[tool]["output_tokens"] += row.get("output_tokens") or 0
            merged[tool]["request_count"] += row.get("request_count") or 0

        result = []
        for data in merged.values():
            result.append(
                {
                    "date": data["date"],
                    "tool_name": data["tool_name"],
                    "tokens_used": data["tokens_used"],
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "cache_tokens": data["cache_tokens"],
                    "request_count": data["request_count"],
                    "models_used": None,
                    "hosts": data["hosts"],
                }
            )

        return result

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_usage_summary(
        self,
        host_name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, dict]:
        """Get usage summary for all tools, merging daily_messages and agent_sessions.

        Issue #2938: /api/summary previously only queried daily_messages, missing
        local WebUI session data. Now merges daily_messages (CLI fetch scripts,
        excluding session_sync dual-write) with agent_sessions (WebUI local/remote/
        terminal sessions).

        Args:
            host_name: Optional host name filter.
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            tenant_id: Optional tenant ID filter.

        Returns:
            Dict[str, Dict]: Summary data keyed by tool name.
        """
        dm_summary = self.usage_repo.get_summary_by_tool(
            host_name=host_name,
            start_date=start_date,
            end_date=end_date,
            tenant_id=tenant_id,
        )

        session_summary = self.usage_repo.get_session_summary_by_tool(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name,
            tenant_id=tenant_id,
        )

        merged: dict[str, dict] = {}
        for source in (dm_summary, session_summary):
            for tool, data in source.items():
                if tool not in merged:
                    merged[tool] = {
                        "days_count": 0,
                        "total_tokens": 0,
                        "avg_tokens": 0,
                        "total_requests": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "first_date": None,
                        "last_date": None,
                    }
                m = merged[tool]
                m["total_tokens"] += data["total_tokens"] or 0
                m["total_requests"] += data["total_requests"] or 0
                m["total_input_tokens"] += data["total_input_tokens"] or 0
                m["total_output_tokens"] += data["total_output_tokens"] or 0
                m["days_count"] += data["days_count"] or 0
                if data.get("first_date"):
                    if not m["first_date"] or data["first_date"] < m["first_date"]:
                        m["first_date"] = data["first_date"]
                if data.get("last_date"):
                    if not m["last_date"] or data["last_date"] > m["last_date"]:
                        m["last_date"] = data["last_date"]

        for m in merged.values():
            m["avg_tokens"] = round(m["total_tokens"] / max(m["days_count"], 1), 2)

        return merged

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_tool_usage(
        self,
        tool_name: str,
        days: int = 7,
        host_name: str | None = None,
        tenant_id: int | None = None,
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
        return self.usage_repo.get_usage_by_tool(
            tool_name,
            days,
            host_name=host_name,
            tenant_id=tenant_id,
        )

    def get_date_usage(
        self,
        date: str,
        tool_name: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
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
        return self.usage_repo.get_usage_by_date(date, tool_name, host_name, tenant_id)

    def get_range_usage(
        self,
        start_date: str,
        end_date: str,
        tool_name: str | None = None,
        host_name: str | None = None,
        tenant_id: int | None = None,
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
        return self.usage_repo.get_daily_range(
            start_date,
            end_date,
            tool_name,
            host_name,
            tenant_id,
        )

    @cached(ttl=300, key_prefix="usage", skip_args=[0])
    def get_all_tools(self, tenant_id: int | None = None) -> list[str]:
        """
        Get list of all tools.

        Returns:
            List[str]: List of tool names.
        """
        return self.usage_repo.get_all_tools(tenant_id=tenant_id)

    @cached(ttl=300, key_prefix="usage", skip_args=[0])
    def get_all_hosts(self, tenant_id: int | None = None) -> list[str]:
        """
        Get list of all hosts.

        Returns:
            List[str]: List of host names.
        """
        return self.usage_repo.get_all_hosts(tenant_id=tenant_id)

    def save_usage(
        self,
        date: str,
        tool_name: str,
        tokens_used: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_tokens: int = 0,
        request_count: int = 0,
        models_used: list[str] | None = None,
        host_name: str = "localhost",
        tenant_id: int | None = None,
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
            tenant_id=tenant_id,
        )

    @staticmethod
    def _normalize_date(date_value: str | date | datetime) -> str:
        """Normalize date value to ISO format string (YYYY-MM-DD).

        Issue #3166: Different data sources return different date types:
        - get_daily_by_tool() returns string dates
        - get_session_trend_by_tool() returns datetime.date objects

        This method ensures consistent date format for merging and sorting.

        Args:
            date_value: Date value from any source (str, datetime.date, datetime).

        Returns:
            str: ISO format date string (YYYY-MM-DD).
        """
        if isinstance(date_value, str):
            return date_value
        if isinstance(date_value, datetime):
            return date_value.date().isoformat()
        # At this point, date_value is datetime.date
        return date_value.isoformat()

    @cached(ttl=60, key_prefix="usage", skip_args=[0])
    def get_trend_data(
        self,
        start_date: str,
        end_date: str,
        host_name: str | None = None,
        tenant_id: int | None = None,
    ) -> list[dict]:
        """
        Get usage trend data aggregated by date and tool.

        Issue #3030: Merge daily_stats (CLI data) with agent_sessions (WebUI data)
        to ensure trend charts include all session types.

        Issue #3166: Normalize date values to consistent format before merging
        and sorting to avoid TypeError from mixed date/str types.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.
            tenant_id: Optional tenant ID filter.

        Returns:
            List[Dict]: List of usage records by date and tool.
        """
        # 1. Get trend data from daily_stats (CLI fetch scripts data)
        dm_trend = self.usage_repo.get_daily_by_tool(start_date, end_date, host_name, tenant_id)

        # 2. Get trend data from agent_sessions (WebUI local/remote/terminal sessions)
        session_trend = self.usage_repo.get_session_trend_by_tool(
            start_date, end_date, host_name, tenant_id
        )

        # 3. Merge both data sources with normalized dates
        merged: dict[tuple, dict] = {}
        for entry in dm_trend:
            normalized_date = self._normalize_date(entry["date"])
            key = (normalized_date, entry["tool_name"])
            if key in merged:
                merged[key]["tokens"] += entry.get("tokens", 0)
            else:
                merged[key] = {
                    "date": normalized_date,
                    "tool": entry["tool_name"],
                    "tokens": entry.get("tokens", 0),
                }

        for entry in session_trend:
            normalized_date = self._normalize_date(entry["date"])
            key = (normalized_date, entry["tool_name"])
            if key in merged:
                merged[key]["tokens"] += entry.get("tokens", 0)
            else:
                merged[key] = {
                    "date": normalized_date,
                    "tool": entry["tool_name"],
                    "tokens": entry.get("tokens", 0),
                }

        # Sort by date, then by tool
        result = sorted(merged.values(), key=lambda x: (x["date"], x["tool"]))
        return result
