#!/usr/bin/env python3
"""
Open ACE - Daily Stats Repository

Repository for pre-aggregated daily statistics data.
Provides fast queries for trend analysis by reading from daily_stats table
instead of scanning the large daily_messages table.
"""

import logging
from datetime import datetime
from typing import Optional

from app.repositories.database import Database, is_postgresql
from app.utils.cache import cached

logger = logging.getLogger(__name__)


class DailyStatsRepository:
    """Repository for pre-aggregated daily statistics."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def get_daily_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get daily token totals from pre-aggregated data.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of daily totals with date, tokens, messages.
        """
        conditions = []
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Aggregate by date (sum across all tools/hosts/senders)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                date,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                SUM(message_count) as message_count
            FROM daily_stats
            {where_clause}
            GROUP BY date
            ORDER BY date ASC
        """

        return self.db.fetch_all(query, tuple(params))

    def get_tool_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get tool token totals from pre-aggregated data.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of tool totals.
        """
        conditions = []
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Aggregate by tool_name (sum across all dates/senders)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                tool_name,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                SUM(message_count) as message_count
            FROM daily_stats
            {where_clause}
            GROUP BY tool_name
            ORDER BY total_tokens DESC
        """

        return self.db.fetch_all(query, tuple(params))

    def get_user_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get user token totals from pre-aggregated data.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of user totals.
        """
        conditions = ["sender_name IS NOT NULL"]  # Only include rows with sender
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Aggregate by sender_name (sum across all dates/tools)
        where_clause = f"WHERE {' AND '.join(conditions)}"

        query = f"""
            SELECT
                sender_name,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                SUM(message_count) as message_count
            FROM daily_stats
            {where_clause}
            GROUP BY sender_name
            ORDER BY total_tokens DESC
        """

        return self.db.fetch_all(query, tuple(params))

    @cached(ttl=300, key_prefix="hourly_stats", skip_args=[0])
    def get_hourly_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get hourly usage patterns from pre-aggregated hourly_stats table.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of hourly totals.
        """
        conditions = []
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Query from pre-aggregated hourly_stats table
        query = f"""
            SELECT
                hour,
                SUM(message_count) as requests,
                SUM(total_tokens) as tokens
            FROM hourly_stats
            {where_clause}
            GROUP BY hour
            ORDER BY hour
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Convert to expected format
        result = []
        for row in rows:
            result.append(
                {
                    "hour": int(row["hour"]),
                    "tokens": row["tokens"] or 0,
                    "requests": row["requests"] or 0,
                }
            )

        return result

    def get_conversation_stats(self, host_name: Optional[str] = None) -> dict:
        """
        Get conversation statistics from pre-aggregated data.

        This method calculates conversation stats from daily_stats
        instead of scanning daily_messages.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict: Conversation statistics.
        """
        conditions = []
        params = []

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Get basic stats from daily_stats
        query = f"""
            SELECT
                SUM(message_count) as total_messages,
                SUM(total_tokens) as total_tokens
            FROM daily_stats
            {where_clause}
        """

        result = self.db.fetch_one(query, tuple(params))

        if not result:
            return {
                "total_conversations": 0,
                "total_messages": 0,
                "total_tokens": 0,
                "average_messages_per_conversation": 0,
                "average_tokens_per_conversation": 0,
                "avg_conversation_length": 0,
            }

        total_messages = result.get("total_messages", 0) or 0
        total_tokens = result.get("total_tokens", 0) or 0

        # Estimate conversations from unique dates (approximation)
        # This is faster than counting distinct conversation_ids
        query_dates = f"""
            SELECT COUNT(DISTINCT date) as unique_dates
            FROM daily_stats
            {where_clause}
        """
        dates_result = self.db.fetch_one(query_dates, tuple(params))
        unique_dates = dates_result.get("unique_dates", 1) if dates_result else 1

        # Estimate: each day has roughly total_messages / unique_tools conversations
        query_tools = f"""
            SELECT COUNT(DISTINCT tool_name) as unique_tools
            FROM daily_stats
            {where_clause}
        """
        tools_result = self.db.fetch_one(query_tools, tuple(params))
        unique_tools = tools_result.get("unique_tools", 1) if tools_result else 1

        # Rough estimate: conversations = unique_dates * unique_tools
        estimated_conversations = unique_dates * unique_tools

        return {
            "total_conversations": estimated_conversations,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "average_messages_per_conversation": (
                total_messages / estimated_conversations if estimated_conversations > 0 else 0
            ),
            "average_tokens_per_conversation": (
                total_tokens / estimated_conversations if estimated_conversations > 0 else 0
            ),
            "avg_conversation_length": (
                total_messages / estimated_conversations if estimated_conversations > 0 else 0
            ),
        }

    def get_batch_aggregates(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> dict:
        """
        Get all aggregates in a single query from pre-aggregated data.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            Dict: Aggregate statistics.
        """
        conditions = []
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                SUM(message_count) as total_messages,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                COUNT(DISTINCT tool_name) as unique_tools,
                COUNT(DISTINCT host_name) as unique_hosts,
                COUNT(DISTINCT sender_name) as unique_users,
                COUNT(DISTINCT date) as unique_days
            FROM daily_stats
            {where_clause}
        """

        result = self.db.fetch_one(query, tuple(params))

        if not result:
            return {
                "total_messages": 0,
                "total_tokens": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "unique_tools": 0,
                "unique_hosts": 0,
                "unique_users": 0,
                "unique_days": 0,
            }

        return {
            "total_messages": result.get("total_messages", 0) or 0,
            "total_tokens": result.get("total_tokens", 0) or 0,
            "total_input_tokens": result.get("total_input_tokens", 0) or 0,
            "total_output_tokens": result.get("total_output_tokens", 0) or 0,
            "unique_tools": result.get("unique_tools", 0) or 0,
            "unique_hosts": result.get("unique_hosts", 0) or 0,
            "unique_users": result.get("unique_users", 0) or 0,
            "unique_days": result.get("unique_days", 0) or 0,
        }

    def refresh_stats(self, date: Optional[str] = None) -> bool:
        """
        Refresh daily_stats from daily_messages.

        Args:
            date: Optional specific date to refresh. If None, refreshes all.

        Returns:
            bool: True if successful.
        """
        try:
            now = datetime.utcnow()

            if date:
                # Refresh specific date
                date_condition = "date = ?"
                params = (date,)
            else:
                # Refresh all
                date_condition = "1=1"
                params = ()

            if is_postgresql():
                # Delete existing stats for the date(s)
                self.db.execute(
                    f"DELETE FROM daily_stats WHERE {date_condition}",
                    params,
                )

                # Insert new stats
                self.db.execute(
                    f"""
                    INSERT INTO daily_stats
                    (date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        tool_name,
                        host_name,
                        sender_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages
                    WHERE {date_condition}
                    GROUP BY date, tool_name, host_name, sender_name
                    """,
                    (now,) + params,
                )
            else:
                # SQLite: use INSERT OR REPLACE
                self.db.execute(
                    f"""
                    INSERT OR REPLACE INTO daily_stats
                    (date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        tool_name,
                        host_name,
                        sender_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages
                    WHERE {date_condition}
                    GROUP BY date, tool_name, host_name, sender_name
                    """,
                    (now,) + params,
                )

            logger.info(f"Daily stats refreshed for {date or 'all dates'}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh daily stats: {e}")
            return False

    def needs_refresh(self) -> bool:
        """
        Check if daily_stats needs to be refreshed.

        Returns:
            bool: True if stats are empty or stale (missing recent data).
        """
        # Check if daily_stats is empty
        query = "SELECT COUNT(*) as count FROM daily_stats"
        result = self.db.fetch_one(query)
        if not result or result["count"] == 0:
            return True

        # Check if daily_stats has today's data
        # Compare max date in daily_stats vs max date in daily_messages
        from datetime import datetime

        datetime.now().strftime("%Y-%m-%d")

        stats_max_date_query = "SELECT MAX(date) as max_date FROM daily_stats"
        stats_result = self.db.fetch_one(stats_max_date_query)
        stats_max_date = stats_result.get("max_date") if stats_result else None

        messages_max_date_query = "SELECT MAX(date) as max_date FROM daily_messages"
        messages_result = self.db.fetch_one(messages_max_date_query)
        messages_max_date = messages_result.get("max_date") if messages_result else None

        # If daily_messages has newer data than daily_stats, need refresh
        if messages_max_date and stats_max_date:
            if messages_max_date > stats_max_date:
                logger.info(
                    f"daily_stats stale: messages max date {messages_max_date} > stats max date {stats_max_date}"
                )
                return True

        return False

    def refresh_hourly_stats(self, date: Optional[str] = None) -> bool:
        """
        Refresh hourly_stats from daily_messages.

        Args:
            date: Optional specific date to refresh. If None, refreshes all.

        Returns:
            bool: True if successful.
        """
        try:
            now = datetime.utcnow()

            if date:
                # Refresh specific date
                date_condition = "date = ?"
                params = (date,)
            else:
                # Refresh all
                date_condition = "1=1"
                params = ()

            if is_postgresql():
                # Delete existing stats for the date(s)
                self.db.execute(
                    f"DELETE FROM hourly_stats WHERE {date_condition}",
                    params,
                )

                # Insert new stats - convert UTC hour to CST (UTC+8)
                self.db.execute(
                    f"""
                    INSERT INTO hourly_stats
                    (date, hour, tool_name, host_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        MOD(EXTRACT(HOUR FROM timestamp::timestamp)::INTEGER + 8, 24) as hour,
                        tool_name,
                        host_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages
                    WHERE {date_condition} AND timestamp IS NOT NULL
                    GROUP BY date, MOD(EXTRACT(HOUR FROM timestamp::timestamp)::INTEGER + 8, 24), tool_name, host_name
                    """,
                    (now,) + params,
                )
            else:
                # SQLite: use INSERT OR REPLACE
                self.db.execute(
                    f"""
                    INSERT OR REPLACE INTO hourly_stats
                    (date, hour, tool_name, host_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        (CAST(strftime('%H', timestamp) AS INTEGER) + 8) % 24 as hour,
                        tool_name,
                        host_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages
                    WHERE {date_condition} AND timestamp IS NOT NULL
                    GROUP BY date, (CAST(strftime('%H', timestamp) AS INTEGER) + 8) % 24, tool_name, host_name
                    """,
                    (now,) + params,
                )

            logger.info(f"Hourly stats refreshed for {date or 'all dates'}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh hourly stats: {e}")
            return False
