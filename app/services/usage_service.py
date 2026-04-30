#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Usage Service

Business logic for usage data operations.
"""

import logging
from datetime import datetime
from typing import Optional

from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached

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
        Get today's usage data, merged by tool_name.

        Args:
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of merged usage records.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        entries = self.usage_repo.get_usage_by_date(today, tool_name, host_name)

        # Merge entries by tool_name (combine all hosts)
        # Use sets for O(1) lookup instead of O(n) list lookup
        merged = {}
        for entry in entries:
            tool = entry.get("tool_name", "unknown")
            if tool not in merged:
                merged[tool] = {
                    "date": entry.get("date"),
                    "tool_name": tool,
                    "tokens_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_tokens": 0,
                    "request_count": 0,
                    "models_used_set": set(),  # Use set for O(1) lookup
                    "hosts_set": set(),  # Use set for O(1) lookup
                }

            merged[tool]["tokens_used"] += entry.get("tokens_used", 0)
            merged[tool]["input_tokens"] += entry.get("input_tokens", 0)
            merged[tool]["output_tokens"] += entry.get("output_tokens", 0)
            merged[tool]["cache_tokens"] += entry.get("cache_tokens", 0)
            merged[tool]["request_count"] += entry.get("request_count", 0)

            if entry.get("models_used"):
                merged[tool]["models_used_set"].update(entry.get("models_used", []))

            host = entry.get("host_name", "unknown")
            merged[tool]["hosts_set"].add(host)

        # Convert to list and clean up
        result = []
        for tool, data in merged.items():
            # Convert sets to lists for output
            models_used = list(data["models_used_set"]) if data["models_used_set"] else None
            hosts = list(data["hosts_set"])
            result.append(
                {
                    "date": data["date"],
                    "tool_name": data["tool_name"],
                    "tokens_used": data["tokens_used"],
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "cache_tokens": data["cache_tokens"],
                    "request_count": data["request_count"],
                    "models_used": models_used,
                    "hosts": hosts,
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
