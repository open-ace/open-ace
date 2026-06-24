"""
Open ACE - Message Repository

Repository for message data access operations.
"""

import logging
from typing import Any, Optional

from app.repositories.database import Database, escape_like
from app.utils.cache import cached
from app.utils.roles import normalize_message_role
from app.utils.senders import is_valid_sender
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)


class MessageRepository:
    """Repository for message data operations."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def save_message(
        self,
        date: str,
        tool_name: str,
        message_id: str,
        role: str,
        host_name: str = "localhost",
        parent_id: Optional[str] = None,
        content: Optional[str] = None,
        full_entry: Optional[str] = None,
        tokens_used: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: Optional[str] = None,
        timestamp: Optional[str] = None,
        sender_id: Optional[str] = None,
        sender_name: Optional[str] = None,
        message_source: Optional[str] = None,
        feishu_conversation_id: Optional[str] = None,
        group_subject: Optional[str] = None,
        is_group_chat: Optional[int] = None,
        agent_session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_id: Optional[int] = None,
        project_path: Optional[str] = None,
    ) -> bool:
        """
        Save a message to the database.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Name of the tool.
            message_id: Unique message identifier.
            role: Message role (user, assistant, system, tool). Variant
                tool-result spellings (toolResult, tool_result) are normalized
                to ``tool`` at the write boundary.
            host_name: Host name.
            parent_id: Parent message ID.
            content: Message content.
            full_entry: Full message entry JSON.
            tokens_used: Total tokens used.
            input_tokens: Input tokens.
            output_tokens: Output tokens.
            model: Model name.
            timestamp: Message timestamp.
            sender_id: Sender ID.
            sender_name: Sender name.
            message_source: Message source.
            feishu_conversation_id: Feishu conversation ID.
            group_subject: Group subject.
            is_group_chat: Is group chat flag.
            agent_session_id: Agent session ID (tool process session).
            conversation_id: Conversation ID (one round of conversation).

        Returns:
            bool: True if successful.
        """
        from app.repositories.database import is_postgresql

        # Normalize at the write boundary so variant tool names (qwen-code,
        # QWEN, ...) can never split downstream aggregates (daily_stats,
        # hourly_stats, ROI cost-breakdown) into duplicate slices.
        tool_name = normalize_tool_name(tool_name)

        # Normalize the message role at the write boundary so variant
        # tool-result spellings (toolResult / tool_result) collapse to the
        # canonical ``tool``. Without this, conversations produced by different
        # write paths stored different role values and the conversation-detail
        # role filter showed "no messages found" for one of them.
        role = normalize_message_role(role)

        if is_postgresql():
            self.db.execute(
                """
                INSERT INTO daily_messages
                (date, tool_name, host_name, message_id, parent_id, role, content,
                 full_entry, tokens_used, input_tokens, output_tokens, model,
                 timestamp, sender_id, sender_name, message_source,
                 feishu_conversation_id, group_subject, is_group_chat,
                 agent_session_id, conversation_id, user_id, project_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (date, tool_name, host_name, message_id) DO UPDATE SET
                    parent_id = EXCLUDED.parent_id,
                    role = EXCLUDED.role,
                    content = EXCLUDED.content,
                    full_entry = EXCLUDED.full_entry,
                    tokens_used = EXCLUDED.tokens_used,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    model = EXCLUDED.model,
                    timestamp = EXCLUDED.timestamp,
                    sender_id = EXCLUDED.sender_id,
                    sender_name = EXCLUDED.sender_name,
                    message_source = EXCLUDED.message_source,
                    feishu_conversation_id = EXCLUDED.feishu_conversation_id,
                    group_subject = EXCLUDED.group_subject,
                    is_group_chat = EXCLUDED.is_group_chat,
                    agent_session_id = EXCLUDED.agent_session_id,
                    conversation_id = EXCLUDED.conversation_id,
                    user_id = EXCLUDED.user_id,
                    project_path = EXCLUDED.project_path
            """,
                (
                    date,
                    tool_name,
                    host_name,
                    message_id,
                    parent_id,
                    role,
                    content,
                    full_entry,
                    tokens_used,
                    input_tokens,
                    output_tokens,
                    model,
                    timestamp,
                    sender_id,
                    sender_name,
                    message_source,
                    feishu_conversation_id,
                    group_subject,
                    is_group_chat,
                    agent_session_id,
                    conversation_id,
                    user_id,
                    project_path,
                ),
            )
        else:
            self.db.execute(
                """
                INSERT OR REPLACE INTO daily_messages
                (date, tool_name, host_name, message_id, parent_id, role, content,
                 full_entry, tokens_used, input_tokens, output_tokens, model,
                 timestamp, sender_id, sender_name, message_source,
                 feishu_conversation_id, group_subject, is_group_chat,
                 agent_session_id, conversation_id, user_id, project_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    date,
                    tool_name,
                    host_name,
                    message_id,
                    parent_id,
                    role,
                    content,
                    full_entry,
                    tokens_used,
                    input_tokens,
                    output_tokens,
                    model,
                    timestamp,
                    sender_id,
                    sender_name,
                    message_source,
                    feishu_conversation_id,
                    group_subject,
                    is_group_chat,
                    agent_session_id,
                    conversation_id,
                    user_id,
                    project_path,
                ),
            )

        logger.debug(f"Saved message: {date} - {tool_name} - {message_id}")
        return True

    def get_messages_by_date(
        self,
        date: str,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        role: Optional[str] = None,
        sender_name: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict]:
        """
        Get messages for a specific date.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            role: Optional role filter.
            sender_name: Optional sender name filter.
            search: Optional search term for content.
            limit: Optional limit on number of results.
            offset: Offset for pagination.

        Returns:
            List[Dict]: List of message records.
        """
        conditions = ["date = ?"]
        params = [date]

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if role:
            # Support multiple roles (comma-separated)
            roles = [r.strip() for r in role.split(",")]
            if len(roles) == 1:
                conditions.append("role = ?")
                params.append(roles[0])
            else:
                placeholders = ",".join(["?" for _ in roles])
                conditions.append(f"role IN ({placeholders})")
                params.extend(roles)

        if sender_name:
            conditions.append("sender_name = ?")
            params.append(sender_name)

        if search:
            conditions.append("content LIKE ?")
            params.append(f"%{escape_like(search)}%")

        query = f"""
            SELECT * FROM daily_messages
            WHERE {" AND ".join(conditions)}
            ORDER BY timestamp DESC
        """

        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"

        return self.db.fetch_all(query, tuple(params))

    def get_messages_by_date_range(
        self,
        start_date: str,
        end_date: str,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        role: Optional[str] = None,
        sender_name: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict]:
        """
        Get messages for a date range.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            role: Optional role filter (comma-separated for multiple roles).
            search: Optional search term for content.
            limit: Optional limit on number of results.
            offset: Offset for pagination.

        Returns:
            List[Dict]: List of message records.
        """
        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if role:
            # Support multiple roles (comma-separated)
            roles = [r.strip() for r in role.split(",")]
            if len(roles) == 1:
                conditions.append("role = ?")
                params.append(roles[0])
            else:
                placeholders = ",".join(["?" for _ in roles])
                conditions.append(f"role IN ({placeholders})")
                params.extend(roles)

        if sender_name:
            conditions.append("sender_name = ?")
            params.append(sender_name)

        if search:
            conditions.append("content LIKE ?")
            params.append(f"%{escape_like(search)}%")

        query = f"""
            SELECT * FROM daily_messages
            WHERE {" AND ".join(conditions)}
            ORDER BY date DESC, timestamp DESC
        """

        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"

        return self.db.fetch_all(query, tuple(params))

    def get_conversation_history(
        self,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        sender_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        Get conversation history with aggregated statistics.

        Args:
            date: Optional date filter.
            start_date: Optional start date filter (defaults to 90 days ago).
            end_date: Optional end date filter.
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            sender_name: Optional sender name filter.
            limit: Limit on number of results.
            offset: Offset for pagination.

        Returns:
            List[Dict]: List of conversation records.
        """
        from datetime import datetime, timedelta, timezone

        conditions: list[str] = []
        params: list[Any] = []

        if date:
            conditions.append("date = ?")
            params.append(date)
        else:
            if not start_date:
                start_date = (
                    datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
                ).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            conditions.append("date >= ?")
            params.append(start_date)
            conditions.append("date <= ?")
            params.append(end_date)

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if sender_name:
            conditions.append("sender_name = ?")
            params.append(sender_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Add condition to filter out records without any session ID
        id_filter = (
            "COALESCE(conversation_id, feishu_conversation_id, agent_session_id) IS NOT NULL"
        )
        if where_clause:
            where_clause = f"{where_clause} AND {id_filter}"
        else:
            where_clause = f"WHERE {id_filter}"

        # Use COALESCE to get the first available session ID
        # Priority: feishu_conversation_id > agent_session_id > conversation_id
        query = f"""
            SELECT
                COALESCE(conversation_id, feishu_conversation_id, agent_session_id) as conversation_id,
                agent_session_id as session_id,
                tool_name,
                host_name,
                sender_name,
                MAX(sender_id) as sender_id,
                MAX(date) as date,
                COUNT(*) as message_count,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                MIN(timestamp) as first_message_time,
                MAX(timestamp) as last_message_time
            FROM daily_messages
            {where_clause}
            GROUP BY COALESCE(conversation_id, feishu_conversation_id, agent_session_id), agent_session_id, tool_name, host_name, sender_name
            ORDER BY last_message_time DESC
            LIMIT ? OFFSET ?
        """

        params.extend([limit, offset])
        rows = self.db.fetch_all(query, tuple(params))
        # Normalize tool_name in results for display
        for row in rows:
            row["tool_name"] = normalize_tool_name(row["tool_name"])
        return rows

    def count_conversations(
        self,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        sender_name: Optional[str] = None,
    ) -> int:
        """Count total conversations matching filters."""
        from datetime import datetime, timedelta, timezone

        conditions = []
        params = []

        if date:
            conditions.append("date = ?")
            params.append(date)
        else:
            if not start_date:
                start_date = (
                    datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
                ).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            conditions.append("date >= ?")
            params.append(start_date)
            conditions.append("date <= ?")
            params.append(end_date)

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)
        if sender_name:
            conditions.append("sender_name = ?")
            params.append(sender_name)

        id_filter = (
            "COALESCE(conversation_id, feishu_conversation_id, agent_session_id) IS NOT NULL"
        )
        conditions.append(id_filter)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        query = f"""
            SELECT COUNT(*) as total FROM (
                SELECT 1 FROM daily_messages
                {where_clause}
                GROUP BY COALESCE(conversation_id, feishu_conversation_id, agent_session_id),
                         agent_session_id, tool_name, host_name, sender_name
            ) sub
        """

        result = self.db.fetch_one(query, tuple(params))
        return int(result["total"]) if result else 0

    def get_conversation_timeline(self, session_id: str) -> list[dict]:
        """
        Get timeline of messages for a conversation.

        Args:
            session_id: Conversation/session ID.

        Returns:
            List[Dict]: List of messages in the conversation.
        """
        # Use COALESCE to match session_id from multiple possible fields
        query = """
            SELECT * FROM daily_messages
            WHERE COALESCE(conversation_id, feishu_conversation_id, agent_session_id) = ?
            ORDER BY timestamp ASC
        """

        return self.db.fetch_all(query, (session_id,))

    def get_conversation_details(self, session_id: str) -> Optional[dict]:
        """
        Get details of a conversation.

        Args:
            session_id: Conversation/session ID.

        Returns:
            Optional[Dict]: Conversation details or None.
        """
        # Use COALESCE to match session_id from multiple possible fields
        query = """
            SELECT
                COALESCE(conversation_id, feishu_conversation_id, agent_session_id) as conversation_id,
                agent_session_id as session_id,
                tool_name,
                host_name,
                sender_name,
                MAX(sender_id) as sender_id,
                MAX(date) as date,
                COUNT(*) as message_count,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                MIN(timestamp) as first_message_time,
                MAX(timestamp) as last_message_time
            FROM daily_messages
            WHERE COALESCE(conversation_id, feishu_conversation_id, agent_session_id) = ?
            GROUP BY COALESCE(conversation_id, feishu_conversation_id, agent_session_id)
        """

        return self.db.fetch_one(query, (session_id,))

    def get_all_senders(self, host_name: Optional[str] = None) -> list[str]:
        """
        Get list of all senders.

        Args:
            host_name: Optional host name filter.

        Returns:
            List[str]: List of sender names.
        """
        conditions = []
        params = []

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT DISTINCT sender_name
            FROM daily_messages
            {where_clause}
            ORDER BY sender_name
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Filter out abnormal sender names (Feishu Open IDs, etc.)
        return [row["sender_name"] for row in rows if is_valid_sender(row["sender_name"])]

    def count_messages(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        sender_name: Optional[str] = None,
        role: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """
        Count messages matching filters.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            sender_name: Optional sender name filter.
            role: Optional role filter (comma-separated for multiple roles).
            search: Optional search term for content.

        Returns:
            int: Number of matching messages.
        """
        conditions = []
        params = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if sender_name:
            conditions.append("sender_name = ?")
            params.append(sender_name)

        if role:
            # Support multiple roles (comma-separated)
            roles = [r.strip() for r in role.split(",")]
            if len(roles) == 1:
                conditions.append("role = ?")
                params.append(roles[0])
            else:
                placeholders = ",".join(["?" for _ in roles])
                conditions.append(f"role IN ({placeholders})")
                params.extend(roles)

        if search:
            conditions.append("content LIKE ?")
            params.append(f"%{escape_like(search)}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"SELECT COUNT(*) as count FROM daily_messages {where_clause}"

        result = self.db.fetch_one(query, tuple(params))
        return int(result["count"]) if result else 0

    def get_user_token_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get total tokens per user for segmentation analysis.

        Merges users by user_id when available, falling back to sender_name for unmapped accounts.
        This solves the issue where the same user appears multiple times with different
        sender_name formats (e.g., user1-host1-qwen, user1-host2-qwen).

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of user token totals with unified_username field.
        """
        conditions = ["dm.date IS NOT NULL"]
        params = []

        if start_date:
            conditions.append("dm.date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("dm.date <= ?")
            params.append(end_date)

        if host_name:
            conditions.append("dm.host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        # Use LEFT JOIN to get unified username from users table
        # Group by user_id when available, fallback to sender_name for unmapped accounts
        # Use MAX/MIN for sender_name and sender_id as PostgreSQL requires all non-grouped
        # columns to be in GROUP BY or aggregated
        query = f"""
            SELECT
                COALESCE(dm.user_id, -1) as user_id,
                COALESCE(u.username, dm.sender_name) as unified_username,
                MAX(dm.sender_name) as sender_name,
                MAX(dm.sender_id) as sender_id,
                SUM(dm.tokens_used) as total_tokens,
                SUM(dm.input_tokens) as total_input_tokens,
                SUM(dm.output_tokens) as total_output_tokens,
                COUNT(*) as message_count
            FROM daily_messages dm
            LEFT JOIN users u ON dm.user_id = u.id
            {where_clause}
            GROUP BY COALESCE(dm.user_id, -1), COALESCE(u.username, dm.sender_name)
            ORDER BY total_tokens DESC
        """

        return self.db.fetch_all(query, tuple(params))

    def get_hourly_usage(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get hourly usage patterns from message timestamps.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of hourly usage data with hour, tokens, requests.
            Hour is converted from UTC to CST (UTC+8).
        """
        from app.repositories.database import is_postgresql

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

        # Use optimized SQL without regex validation for better performance
        if is_postgresql():
            # Simplified query - direct timestamp cast without regex
            query = f"""
                SELECT
                    EXTRACT(HOUR FROM timestamp::timestamp) as hour,
                    COUNT(*) as requests,
                    SUM(tokens_used) as tokens
                FROM daily_messages
                {where_clause}
                {"AND" if conditions else "WHERE"} timestamp IS NOT NULL AND timestamp::text != ''
                GROUP BY EXTRACT(HOUR FROM timestamp::timestamp)
                ORDER BY hour
            """
        else:
            query = f"""
                SELECT
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                    COUNT(*) as requests,
                    SUM(tokens_used) as tokens
                FROM daily_messages
                {where_clause}
                {"AND" if conditions else "WHERE"} timestamp IS NOT NULL AND timestamp != ''
                GROUP BY strftime('%H', timestamp)
                ORDER BY hour
            """

        rows = self.db.fetch_all(query, tuple(params))

        # Convert UTC hour to CST (UTC+8)
        # Note: This assumes the timestamp in database is stored in UTC
        # For SQLite, strftime('%H', timestamp) returns the hour as stored
        # We need to add 8 hours and handle day overflow
        aggregated = {}
        for row in rows:
            utc_hour = row["hour"]
            cst_hour = (utc_hour + 8) % 24

            key = cst_hour
            if key not in aggregated:
                aggregated[key] = {"hour": cst_hour, "tokens": 0, "requests": 0}

            aggregated[key]["tokens"] += row["tokens"] or 0
            aggregated[key]["requests"] += row["requests"] or 0

        # Convert to list and sort by hour
        result = list(aggregated.values())
        result.sort(key=lambda x: x["hour"])

        return result

    def get_daily_token_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get total tokens per day for trend analysis.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of daily token totals.
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
                date,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                COUNT(*) as message_count
            FROM daily_messages
            {where_clause}
            GROUP BY date
            ORDER BY date ASC
        """

        return self.db.fetch_all(query, tuple(params))

    def get_daily_tool_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get total tokens per day per tool.

        Used to identify the top contributing tool for each anomaly date
        (a single grouped query over the whole range, avoiding per-date N+1).

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: Rows of {date, tool_name, total_tokens} with normalized
            tool names merged across aliases, sorted by date then tokens desc.
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
                date,
                tool_name,
                SUM(tokens_used) as total_tokens
            FROM daily_messages
            {where_clause}
            GROUP BY date, tool_name
            ORDER BY date ASC, total_tokens DESC
        """

        rows = self.db.fetch_all(query, tuple(params))

        # Normalize tool names and merge aliases within each date. Coerce types
        # explicitly so the merged map is statically typed (date:str, tokens:int),
        # and sort the typed pairs (avoids arithmetic on loosely-typed dicts).
        merged: dict[tuple[str, str], int] = {}
        for row in rows:
            date = str(row.get("date") or "")
            tool = normalize_tool_name(row.get("tool_name", "unknown"))
            tokens = int(row.get("total_tokens") or 0)
            key = (date, tool)
            merged[key] = merged.get(key, 0) + tokens

        ordered = sorted(merged.items(), key=lambda kv: (kv[0][0], -kv[1]))
        return [
            {"date": date, "tool_name": tool, "total_tokens": tokens}
            for (date, tool), tokens in ordered
        ]

    def get_tool_token_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Get total tokens per tool for comparison analysis.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of tool token totals.
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
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                COUNT(*) as message_count
            FROM daily_messages
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

    @cached(ttl=300, key_prefix="conv_summary", skip_args=[0])
    def get_conversation_stats_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> dict:
        """
        Get conversation statistics summary without fetching full history.

        Single aggregate query over the SAME ``id_filter`` set, so the
        returned ``total_conversations`` (distinct sessions, the denominator)
        and the ``total_messages`` / ``total_tokens`` sums (the numerators)
        share one scope. This makes per-session averages scope-consistent and
        is the single source of truth for ``get_batch_analysis``,
        ``get_key_metrics`` and the standalone ``get_conversation_stats``.

        Computes real distinct conversation count, total messages, multi-turn
        ratio and per-conversation averages directly from ``daily_messages``
        (grain: 1 row = 1 message). Supports date-range filtering so the batch
        and standalone analysis endpoints share one consistent scope. This
        replaces the previous ``unique_dates * unique_tools`` synthetic
        approximation.

        Note: ``COUNT(DISTINCT COALESCE(...))`` cannot use
        ``idx_messages_conversation`` (the COALESCE expression defeats the
        btree); the query is cached at the repo layer (``@cached``) to bound
        the cost of reintroducing a ``daily_messages`` aggregation into the
        batch hot path.

        Args:
            start_date: Optional start date filter (defaults to 30 days ago,
                aligned with the analysis callers).
            end_date: Optional end date filter (defaults to today).
            host_name: Optional host name filter.

        Returns:
            Dict: Conversation statistics summary. ``avg_conversation_length`` is kept
            as a backward-compatible alias of ``average_messages_per_conversation``
            for existing consumers (calculateHealthScore, insights, exports).
        """
        from datetime import datetime, timedelta, timezone

        conditions: list[str] = []
        params: list[Any] = []

        # Default to the analysis callers' 30-day window when unset (aligned
        # with get_batch_analysis / get_key_metrics / the standalone endpoint)
        # so the batch and standalone paths share one consistent default scope.
        if not start_date:
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Session identifier expression — defined once and reused by the WHERE
        # filter and the GROUP BY so the two cannot drift apart.
        session_expr = "COALESCE(conversation_id, feishu_conversation_id, agent_session_id)"

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)
        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Same id_filter set for numerator (SUM) and denominator (COUNT DISTINCT)
        # so per-session averages stay scope-consistent. Only count records that
        # carry a session identifier. If a faster
        # ``COUNT(DISTINCT agent_session_id)`` variant is ever adopted, this
        # filter MUST switch to ``agent_session_id IS NOT NULL`` in tandem.
        id_filter = f"{session_expr} IS NOT NULL"
        conditions.append(id_filter)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        # Single-pass aggregate: group once per session in a derived table, then
        # roll up. One scan of daily_messages yields the distinct conversation
        # count, the total message/token sums and the multi-turn count together
        # (previously this required two separate queries).
        # COALESCE / COUNT(DISTINCT) / SUM are standard SQL (PostgreSQL + SQLite).
        query = f"""
            SELECT
                COUNT(*) AS total_conversations,
                COALESCE(SUM(cnt), 0) AS total_messages,
                COALESCE(SUM(session_tokens), 0) AS total_tokens,
                COALESCE(SUM(CASE WHEN cnt >= 2 THEN 1 ELSE 0 END), 0) AS multi_turn_session_count
            FROM (
                SELECT
                    {session_expr} AS s,
                    COUNT(*) AS cnt,
                    SUM(tokens_used) AS session_tokens
                FROM daily_messages
                {where_clause}
                GROUP BY {session_expr}
            ) AS per_session
        """

        result = self.db.fetch_one(query, tuple(params))

        total_conversations = (result or {}).get("total_conversations", 0) or 0
        if not result or total_conversations == 0:
            # No matching rows (DB returns None) or a real empty set (the derived
            # table has zero groups -> COUNT(*) = 0). SUM(CASE ...) is NULL in the
            # latter, but the guard short-circuits before it is read.
            return {
                "total_conversations": 0,
                "total_messages": 0,
                "total_tokens": 0,
                "multi_turn_session_count": 0,
                "multi_turn_ratio": 0,
                "average_messages_per_conversation": 0,
                "average_tokens_per_conversation": 0,
                "avg_conversation_length": 0,
            }

        total_messages = result.get("total_messages", 0) or 0
        total_tokens = result.get("total_tokens", 0) or 0
        multi_turn_session_count = result.get("multi_turn_session_count", 0) or 0

        multi_turn_ratio = multi_turn_session_count / total_conversations
        average_messages = total_messages / total_conversations

        return {
            "total_conversations": total_conversations,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "multi_turn_session_count": multi_turn_session_count,
            "multi_turn_ratio": multi_turn_ratio,
            "average_messages_per_conversation": average_messages,
            "average_tokens_per_conversation": total_tokens / total_conversations,
            # Backward-compatible alias consumed by calculateHealthScore, insights, exports
            "avg_conversation_length": average_messages,
        }

    def get_daily_range_lightweight(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> list[dict]:
        """
        Get lightweight daily range data for batch analysis.

        This method only returns essential columns needed for aggregation,
        avoiding the expensive retrieval of content and full_entry fields.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of lightweight usage records.
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
                host_name,
                tokens_used,
                input_tokens,
                output_tokens
            FROM daily_messages
            WHERE {" AND ".join(conditions)}
        """

        return self.db.fetch_all(query, tuple(params))

    def get_batch_analysis_aggregates(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> dict:
        """
        Get all aggregates needed for batch analysis in a single query.

        This method replaces multiple separate queries with a single efficient
        aggregation query, dramatically reducing database load and response time.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            host_name: Optional host name filter.

        Returns:
            Dict: All aggregate statistics needed for batch analysis.
        """

        conditions = ["date >= ?", "date <= ?"]
        params = [start_date, end_date]

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        # Single query to get all basic aggregates
        query = f"""
            SELECT
                COUNT(*) as total_messages,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                COUNT(DISTINCT tool_name) as unique_tools,
                COUNT(DISTINCT host_name) as unique_hosts,
                COUNT(DISTINCT sender_name) as unique_users,
                COUNT(DISTINCT date) as unique_days
            FROM daily_messages
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

    def get_user_messages_stats(self, start_date: str, end_date: str, sender_prefix: str) -> dict:
        """
        Get user message statistics summary for insights analysis.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            sender_prefix: Prefix to match sender_name (system_account or username).

        Returns:
            Dict: Statistics summary with total_conversations, total_messages,
                  total_tokens, avg_messages_per_conversation.
        """
        query = """
            SELECT
                COUNT(DISTINCT COALESCE(agent_session_id, conversation_id)) as total_conversations,
                COUNT(*) as total_messages,
                COALESCE(SUM(tokens_used), 0) as total_tokens
            FROM daily_messages
            WHERE date >= ? AND date <= ?
              AND sender_name LIKE ?
              AND role IN ('user', 'assistant')
        """
        result = self.db.fetch_one(query, (start_date, end_date, f"{escape_like(sender_prefix)}%"))

        if result:
            total_conversations = result["total_conversations"] or 0
            total_messages = result["total_messages"] or 0
            total_tokens = result["total_tokens"] or 0
            return {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "avg_messages_per_conversation": (
                    round(total_messages / total_conversations, 1) if total_conversations > 0 else 0
                ),
            }

        return {
            "total_conversations": 0,
            "total_messages": 0,
            "total_tokens": 0,
            "avg_messages_per_conversation": 0,
        }

    def get_user_conversation_samples(
        self,
        start_date: str,
        end_date: str,
        sender_prefix: str,
        limit: int = 5,
    ) -> list[dict]:
        """
        Get sampled conversations for insights analysis.

        Returns up to `limit` conversations with their messages,
        each message truncated to 300 characters.

        Args:
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            sender_prefix: Prefix to match sender_name.
            limit: Maximum number of conversations to sample.

        Returns:
            List[Dict]: List of conversations, each with session_id and messages.
        """
        # Find distinct agent_session_ids that have user messages
        # Use ORDER BY to ensure deterministic sampling (fixes #685)
        safe_prefix = escape_like(sender_prefix)
        session_query = """
            SELECT DISTINCT COALESCE(agent_session_id, conversation_id) as session_id
            FROM daily_messages
            WHERE date >= ? AND date <= ?
              AND sender_name LIKE ?
              AND role = 'user'
              AND COALESCE(agent_session_id, conversation_id) IS NOT NULL
            GROUP BY session_id
            ORDER BY MIN(timestamp) ASC, session_id ASC
            LIMIT ?
        """
        sessions = self.db.fetch_all(
            session_query, (start_date, end_date, f"{safe_prefix}%", limit)
        )

        if not sessions:
            return []

        # For each session, get messages
        conversations = []
        for session in sessions:
            session_id = session["session_id"]
            msg_query = """
                SELECT role, LEFT(content, 300) as content
                FROM daily_messages
                WHERE (agent_session_id = ? OR conversation_id = ?)
                  AND role IN ('user', 'assistant')
                  AND content IS NOT NULL
                  AND content != ''
                ORDER BY timestamp ASC
            """
            try:
                messages = self.db.fetch_all(msg_query, (session_id, session_id))
            except Exception as e:
                logger.warning(f"Skipping session {session_id} due to encoding error: {e}")
                continue
            if messages:
                conversations.append(
                    {
                        "session_id": session_id,
                        "messages": [
                            {"role": m["role"], "content": m["content"]} for m in messages
                        ],
                    }
                )

        return conversations
