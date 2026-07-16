"""
Open ACE - Daily Stats Repository

Repository for pre-aggregated daily statistics data.
Provides fast queries for trend analysis by reading from daily_stats table
instead of scanning the large daily_messages table.
"""

import logging
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Optional

from app.repositories.database import Database, is_postgresql
from app.utils.cache import cached
from app.utils.sender_hash import compute_sender_hash, EMPTY_SENDER_HASH
from app.utils.senders import get_sender_filter_sql
from app.utils.tool_names import normalize_tool_name

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

        rows = self.db.fetch_all(query, tuple(params))

        # Normalize tool names and merge
        merged: dict[str, dict] = {}
        for row in rows:
            tool = normalize_tool_name(row["tool_name"])
            if tool in merged:
                existing = merged[tool]
                existing["total_tokens"] += row["total_tokens"] or 0
                existing["total_input_tokens"] += row["total_input_tokens"] or 0
                existing["total_output_tokens"] += row["total_output_tokens"] or 0
                existing["message_count"] += row["message_count"] or 0
            else:
                merged[tool] = {**row, "tool_name": tool}

        return sorted(merged.values(), key=lambda x: x.get("total_tokens", 0), reverse=True)

    def get_tool_totals_with_range(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> dict[str, dict]:
        """
        Get tool totals with all required fields for summary API.

        This method returns a dict format (keyed by tool_name) with all fields
        required by the frontend ToolSummary type, including days_count, avg_tokens,
        first_date, and last_date.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            Dict[str, Dict]: Summary data keyed by normalized tool name.
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
                tool_name,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                SUM(message_count) as message_count,
                COUNT(DISTINCT date) as days_count,
                MIN(date) as first_date,
                MAX(date) as last_date
            FROM daily_stats
            {where_clause}
            GROUP BY tool_name
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Convert to dict format and merge normalized tool names
        merged: dict[str, dict] = {}
        for row in rows:
            tool = normalize_tool_name(row["tool_name"])
            if tool in merged:
                existing = merged[tool]
                existing["total_tokens"] += row["total_tokens"] or 0
                existing["total_input_tokens"] += row["total_input_tokens"] or 0
                existing["total_output_tokens"] += row["total_output_tokens"] or 0
                existing["total_requests"] += row["message_count"] or 0
                # days_count: use max() as conservative approach (dates may overlap)
                existing["days_count"] = max(existing["days_count"], row["days_count"] or 0)
                # first_date: take earliest
                existing["first_date"] = min(existing["first_date"], row["first_date"] or "")
                # last_date: take latest
                existing["last_date"] = max(existing["last_date"], row["last_date"] or "")
            else:
                merged[tool] = {
                    "total_tokens": row["total_tokens"] or 0,
                    "total_requests": row["message_count"] or 0,
                    "total_input_tokens": row["total_input_tokens"] or 0,
                    "total_output_tokens": row["total_output_tokens"] or 0,
                    "days_count": row["days_count"] or 1,
                    "first_date": row["first_date"] or start_date or "",
                    "last_date": row["last_date"] or end_date or "",
                }

        # Calculate avg_tokens after merging
        for tool, data in merged.items():
            data["avg_tokens"] = (data["total_tokens"] or 0) // max(data["days_count"] or 1, 1)

        return merged

    def get_user_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get user token totals from pre-aggregated data.

        This method merges users by user_id when available, falling back to
        sender_name matching for unmapped accounts. This fixes Issue #626
        where the same user appears multiple times with different sender_name
        formats (e.g., WebUI format and Feishu format).

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of user totals with unified_username field.
        """
        conditions = ["ds.sender_name IS NOT NULL"]  # Only include rows with sender
        params = []

        if start_date:
            conditions.append("ds.date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("ds.date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("ds.host_name = ?")
            params.append(host_name)

        # Filter out invalid sender names (Feishu IDs, placeholder values, etc.)
        # Keep in sync with app/utils/senders.py is_valid_sender()
        conditions.append(get_sender_filter_sql("ds.sender_name"))

        where_clause = f"WHERE {' AND '.join(conditions)}"

        if is_postgresql():
            # PostgreSQL: use subquery for user_id resolution
            # Issue #1573: Use hash fallback for unresolved senders (each counts as unique user)
            query = f"""
                SELECT
                    COALESCE(ds.user_id,
                        (SELECT u.id FROM users u
                         WHERE ds.sender_name LIKE (u.system_account || '-%%')
                            OR ds.sender_name = u.username
                         LIMIT 1),
                        CASE
                            WHEN ds.sender_name = '' THEN {EMPTY_SENDER_HASH}
                            ELSE -ABS(('0x' || LEFT(MD5(ds.sender_name), 16))::BIT(64)::BIGINT)
                        END
                    ) as resolved_user_id,
                    COALESCE(u.username,
                        CASE WHEN ds.sender_name LIKE '%%-%%-%%'
                             THEN SUBSTRING(ds.sender_name FROM '^[^-]+')
                             ELSE ds.sender_name END) as unified_username,
                    SUM(ds.total_tokens) as total_tokens,
                    SUM(ds.total_input_tokens) as total_input_tokens,
                    SUM(ds.total_output_tokens) as total_output_tokens,
                    SUM(ds.message_count) as message_count
                FROM daily_stats ds
                LEFT JOIN users u ON ds.user_id = u.id
                    OR ds.sender_name LIKE (u.system_account || '-%%')
                    OR ds.sender_name = u.username
                {where_clause}
                GROUP BY resolved_user_id, unified_username
                ORDER BY total_tokens DESC
            """
        else:
            # SQLite: use subquery for user_id resolution
            # Issue #1573: Use sender_name in GROUP BY to ensure each unresolved
            # sender counts as unique user. Hash is computed in application layer.
            query = f"""
                SELECT
                    COALESCE(ds.user_id,
                        (SELECT u.id FROM users u
                         WHERE ds.sender_name LIKE (u.system_account || '-%%')
                            OR ds.sender_name = u.username
                         LIMIT 1)) as resolved_user_id,
                    ds.sender_name,
                    COALESCE(u.username,
                        CASE WHEN ds.sender_name LIKE '%%-%%-%%'
                             THEN SUBSTR(ds.sender_name, 1, INSTR(ds.sender_name, '-') - 1)
                             ELSE ds.sender_name END) as unified_username,
                    SUM(ds.total_tokens) as total_tokens,
                    SUM(ds.total_input_tokens) as total_input_tokens,
                    SUM(ds.total_output_tokens) as total_output_tokens,
                    SUM(ds.message_count) as message_count
                FROM daily_stats ds
                LEFT JOIN users u ON ds.user_id = u.id
                    OR ds.sender_name LIKE (u.system_account || '-%%')
                    OR ds.sender_name = u.username
                {where_clause}
                GROUP BY resolved_user_id, ds.sender_name, unified_username
                ORDER BY total_tokens DESC
            """

        rows = self.db.fetch_all(query, tuple(params))

        # For SQLite: compute hash for unresolved senders
        if not is_postgresql():
            for row in rows:
                # Check if resolved_user_id is None or missing (unresolved sender)
                if row.get("resolved_user_id") is None:
                    # Compute hash for unresolved sender
                    row["resolved_user_id"] = compute_sender_hash(row.get("sender_name") or "")
                # Remove sender_name from output (not part of expected schema)
                if "sender_name" in row:
                    del row["sender_name"]

        return rows

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

        .. deprecated::
            This method estimates ``total_conversations`` as
            ``unique_dates * unique_tools`` (an inflated small denominator /
            synthetic approximation) and does not accept a date range, which
            previously caused the batch endpoint to report session counts
            inconsistent with the standalone endpoint. It is no longer called in
            production. ``analysis_service.get_session_stats`` and
            ``message_repo.get_conversation_stats_summary`` are now the single
            source of truth (real distinct conversation count from a single
            scope-consistent query). Retained only for backward compatibility;
            do not wire it back into ``get_batch_analysis``.

        This method calculates conversation stats from daily_stats
        instead of scanning daily_messages.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict: Conversation statistics.
        """
        warnings.warn(
            "daily_stats_repo.get_conversation_stats is deprecated: it returns a "
            "synthetic unique_dates * unique_tools estimate. Use "
            "message_repo.get_conversation_stats_summary (via "
            "analysis_service.get_session_stats) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
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

        This method counts unique_users by user_id instead of sender_name,
        fixing Issue #626 where users were counted multiple times.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            Dict: Aggregate statistics.
        """
        start_time = time.time()

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

        # Filter out invalid sender names for unique_users calculation
        # Keep in sync with is_valid_sender
        sender_filter = get_sender_filter_sql("sender_name")

        if is_postgresql():
            # PostgreSQL: use CASE WHEN to maintain type consistency
            # Issue #1573: Use hash fallback for unresolved senders (each counts as unique user)
            query = f"""
                SELECT
                    SUM(message_count) as total_messages,
                    SUM(total_tokens) as total_tokens,
                    SUM(total_input_tokens) as total_input_tokens,
                    SUM(total_output_tokens) as total_output_tokens,
                    COUNT(DISTINCT tool_name) as unique_tools,
                    COUNT(DISTINCT host_name) as unique_hosts,
                    COUNT(DISTINCT CASE WHEN {sender_filter} THEN
                        CASE
                            WHEN user_id IS NOT NULL THEN user_id
                            ELSE COALESCE(
                                (SELECT u.id FROM users u
                                 WHERE sender_name LIKE (u.system_account || '-%%')
                                    OR sender_name = u.username
                                 LIMIT 1),
                                CASE
                                    WHEN sender_name = '' THEN {EMPTY_SENDER_HASH}
                                    ELSE -ABS(('0x' || LEFT(MD5(sender_name), 16))::BIT(64)::BIGINT)
                                END
                            )
                        END
                    END) as unique_users,
                    COUNT(DISTINCT date) as unique_days
                FROM daily_stats
                {where_clause}
            """

            result = self.db.fetch_one(query, tuple(params))

            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"get_batch_aggregates took {duration_ms:.2f}ms")

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
        else:
            # SQLite: use subquery for user_id resolution
            # Issue #1573: For consistency with PostgreSQL, compute hash in application layer
            # First, get basic aggregates without unique_users
            query = f"""
                SELECT
                    SUM(message_count) as total_messages,
                    SUM(total_tokens) as total_tokens,
                    SUM(total_input_tokens) as total_input_tokens,
                    SUM(total_output_tokens) as total_output_tokens,
                    COUNT(DISTINCT tool_name) as unique_tools,
                    COUNT(DISTINCT host_name) as unique_hosts,
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

            # Compute unique_users separately using application-layer hash
            # This ensures consistency with PostgreSQL's hash algorithm
            sender_query = f"""
                SELECT DISTINCT sender_name, user_id,
                    (SELECT u.id FROM users u
                     WHERE sender_name LIKE (u.system_account || '-%%')
                        OR sender_name = u.username
                     LIMIT 1) as matched_user_id
                FROM daily_stats
                {where_clause}
                AND {sender_filter}
            """
            sender_rows = self.db.fetch_all(sender_query, tuple(params))

            unique_user_ids = set()
            for row in sender_rows:
                if row["user_id"] is not None:
                    unique_user_ids.add(row["user_id"])
                elif row["matched_user_id"] is not None:
                    unique_user_ids.add(row["matched_user_id"])
                else:
                    # Unresolved sender: compute hash
                    hash_id = compute_sender_hash(row["sender_name"] or "")
                    unique_user_ids.add(hash_id)

            result["unique_users"] = len(unique_user_ids)

            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"get_batch_aggregates took {duration_ms:.2f}ms")

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

        This method aggregates daily_messages into daily_stats, populating user_id
        by matching sender_name to users table. This fixes Issue #626 where users
        were counted multiple times due to different sender_name formats.

        Args:
            date: Optional specific date to refresh. If None, refreshes all.

        Returns:
            bool: True if successful.
        """
        start_time = time.time()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if date:
                # Refresh specific date
                date_condition = "date = ?"
                params: tuple[Any, ...] = (date,)
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

                # Insert new stats with user_id populated from users table
                # sender_name formats:
                # 1. WebUI: {system_account}-{hostname}-{tool} -> match users.system_account
                # 2. Feishu: username (real name) -> match users.username
                self.db.execute(
                    f"""
                    INSERT INTO daily_stats
                    (date, tool_name, host_name, sender_name, user_id, total_tokens,
                     total_input_tokens, total_output_tokens, message_count, updated_at)
                    SELECT
                        dm.date,
                        dm.tool_name,
                        dm.host_name,
                        dm.sender_name,
                        COALESCE(dm.user_id,
                            (SELECT u.id FROM users u
                             WHERE dm.sender_name LIKE (u.system_account || '-%%')
                                OR dm.sender_name = u.username
                             LIMIT 1)) as user_id,
                        SUM(dm.tokens_used) as total_tokens,
                        SUM(dm.input_tokens) as total_input_tokens,
                        SUM(dm.output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages dm
                    WHERE {date_condition}
                    GROUP BY dm.date, dm.tool_name, dm.host_name, dm.sender_name,
                             COALESCE(dm.user_id,
                                (SELECT u.id FROM users u
                                 WHERE dm.sender_name LIKE (u.system_account || '-%%')
                                    OR dm.sender_name = u.username
                                 LIMIT 1))
                    """,
                    (now,) + params,
                )
            else:
                # SQLite: use INSERT OR REPLACE with user_id populated
                self.db.execute(
                    f"""
                    INSERT OR REPLACE INTO daily_stats
                    (date, tool_name, host_name, sender_name, user_id, total_tokens,
                     total_input_tokens, total_output_tokens, message_count, updated_at)
                    SELECT
                        dm.date,
                        dm.tool_name,
                        dm.host_name,
                        dm.sender_name,
                        COALESCE(dm.user_id,
                            (SELECT u.id FROM users u
                             WHERE dm.sender_name LIKE (u.system_account || '-%%')
                                OR dm.sender_name = u.username
                             LIMIT 1)) as user_id,
                        SUM(dm.tokens_used) as total_tokens,
                        SUM(dm.input_tokens) as total_input_tokens,
                        SUM(dm.output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        ?
                    FROM daily_messages dm
                    WHERE {date_condition}
                    GROUP BY dm.date, dm.tool_name, dm.host_name, dm.sender_name,
                             COALESCE(dm.user_id,
                                (SELECT u.id FROM users u
                                 WHERE dm.sender_name LIKE (u.system_account || '-%%')
                                    OR dm.sender_name = u.username
                                 LIMIT 1))
                    """,
                    (now,) + params,
                )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"refresh_stats took {duration_ms:.2f}ms for {date or 'all dates'}")
            return True

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Failed to refresh daily stats: {e} (took {duration_ms:.2f}ms)")
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

        # Check if sender_name is missing (NULL values that should have data)
        # Only check rows where sender_name should NOT be NULL (corresponding daily_messages has data)
        null_sender_query = """
            SELECT COUNT(*) as count FROM daily_stats ds
            WHERE ds.sender_name IS NULL
            AND EXISTS (
                SELECT 1 FROM daily_messages dm
                WHERE dm.date = ds.date
                AND dm.tool_name = ds.tool_name
                AND dm.host_name = ds.host_name
                AND dm.sender_name IS NOT NULL
            )
        """
        null_result = self.db.fetch_one(null_sender_query)
        if null_result and null_result.get("count", 0) > 0:
            logger.info(f"daily_stats has {null_result['count']} rows missing sender_name")
            return True

        return False

    def get_data_range(self) -> Optional[dict]:
        """
        Get the actual data range (min and max dates) from daily_stats.

        This method returns the real date range of data in the database,
        which can be used by the frontend "All" button to show the actual
        data span instead of a hardcoded 365 days.

        The data range is always global (not filtered by host) by design.
        The "All" button represents the system's complete data range,
        providing a consistent global perspective even when users switch
        between hosts.

        Returns:
            Optional[Dict]: Data range with min_date and max_date, or None if table is empty.
            Example: {"min_date": "2024-01-01", "max_date": "2024-12-31"}
        """
        query = """
            SELECT
                MIN(date) as min_date,
                MAX(date) as max_date
            FROM daily_stats
        """

        result = self.db.fetch_one(query)

        if not result or not result.get("min_date"):
            return None

        return {
            "min_date": result["min_date"],
            "max_date": result["max_date"],
        }

    def refresh_hourly_stats(self, date: Optional[str] = None) -> bool:
        """
        Refresh hourly_stats from daily_messages.

        Args:
            date: Optional specific date to refresh. If None, refreshes all.

        Returns:
            bool: True if successful.
        """
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if date:
                # Refresh specific date
                date_condition = "date = ?"
                params: tuple[Any, ...] = (date,)
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
