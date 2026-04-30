#!/usr/bin/env python3
"""
Open ACE - Message Repository

Repository for message data access operations.
"""

import logging
from typing import Dict, List, Optional

from app.repositories.database import Database

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
    ) -> bool:
        """
        Save a message to the database.

        Args:
            date: Date string (YYYY-MM-DD).
            tool_name: Name of the tool.
            message_id: Unique message identifier.
            role: Message role (user, assistant, system).
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

        if is_postgresql():
            self.db.execute(
                """
                INSERT INTO daily_messages
                (date, tool_name, host_name, message_id, parent_id, role, content,
                 full_entry, tokens_used, input_tokens, output_tokens, model,
                 timestamp, sender_id, sender_name, message_source,
                 feishu_conversation_id, group_subject, is_group_chat,
                 agent_session_id, conversation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    conversation_id = EXCLUDED.conversation_id
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
                 agent_session_id, conversation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> List[Dict]:
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
            params.append(f"%{search}%")

        query = f"""
            SELECT * FROM daily_messages
            WHERE {' AND '.join(conditions)}
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
    ) -> List[Dict]:
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
            params.append(f"%{search}%")

        query = f"""
            SELECT * FROM daily_messages
            WHERE {' AND '.join(conditions)}
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
    ) -> List[Dict]:
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
        from datetime import datetime, timedelta

        conditions = []
        params = []

        if date:
            conditions.append("date = ?")
            params.append(date)
        else:
            if not start_date:
                start_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.utcnow().strftime("%Y-%m-%d")
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
        return self.db.fetch_all(query, tuple(params))

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
        from datetime import datetime, timedelta

        conditions = []
        params = []

        if date:
            conditions.append("date = ?")
            params.append(date)
        else:
            if not start_date:
                start_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.utcnow().strftime("%Y-%m-%d")
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
        return result["total"] if result else 0

    def get_conversation_timeline(self, session_id: str) -> List[Dict]:
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

    def get_conversation_details(self, session_id: str) -> Optional[Dict]:
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

    def get_all_senders(self, host_name: Optional[str] = None) -> List[str]:
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

        # Filter out abnormal sender names:
        # Feishu user IDs (e.g., "ou_3e479c7f81f8674741d778e8f838f8ed")
        def is_valid_sender(name: str) -> bool:
            if not name:
                return False
            # Filter out Feishu user IDs (starts with "ou_" followed by hex characters)
            if name.startswith("ou_") and len(name) > 10:
                return False
            return True

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
            params.append(f"%{search}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"SELECT COUNT(*) as count FROM daily_messages {where_clause}"

        result = self.db.fetch_one(query, tuple(params))
        return result["count"] if result else 0

    def get_user_token_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get total tokens per user for segmentation analysis.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            List[Dict]: List of user token totals.
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
                sender_name,
                sender_id,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                COUNT(*) as message_count
            FROM daily_messages
            {where_clause}
            GROUP BY sender_name, sender_id
            ORDER BY total_tokens DESC
        """

        return self.db.fetch_all(query, tuple(params))

    def get_hourly_usage(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> List[Dict]:
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
    ) -> List[Dict]:
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

    def get_tool_token_totals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
    ) -> List[Dict]:
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

        return self.db.fetch_all(query, tuple(params))

    def get_conversation_stats_summary(self, host_name: Optional[str] = None) -> Dict:
        """
        Get conversation statistics summary without fetching full history.

        This is a lightweight query for batch analysis that only returns
        aggregate statistics, avoiding the expensive GROUP BY operation
        with full conversation history retrieval.

        Args:
            host_name: Optional host name filter.

        Returns:
            Dict: Conversation statistics summary.
        """
        conditions = []
        params = []

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        # Add condition to filter out records without any session ID
        id_filter = (
            "COALESCE(conversation_id, feishu_conversation_id, agent_session_id) IS NOT NULL"
        )
        if conditions:
            where_clause = f"WHERE {' AND '.join(conditions)} AND {id_filter}"
        else:
            where_clause = f"WHERE {id_filter}"

        # Use a single aggregate query instead of GROUP BY + LIMIT
        query = f"""
            SELECT
                COUNT(DISTINCT COALESCE(conversation_id, feishu_conversation_id, agent_session_id)) as total_conversations,
                COUNT(*) as total_messages,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_messages
            {where_clause}
        """

        result = self.db.fetch_one(query, tuple(params))

        if result:
            total_conversations = result.get("total_conversations", 0) or 0
            total_messages = result.get("total_messages", 0) or 0
            total_tokens = result.get("total_tokens", 0) or 0

            return {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "average_messages_per_conversation": (
                    total_messages / total_conversations if total_conversations > 0 else 0
                ),
                "average_tokens_per_conversation": (
                    total_tokens / total_conversations if total_conversations > 0 else 0
                ),
                "avg_conversation_length": (
                    total_messages / total_conversations if total_conversations > 0 else 0
                ),
            }

        return {
            "total_conversations": 0,
            "total_messages": 0,
            "total_tokens": 0,
            "average_messages_per_conversation": 0,
            "average_tokens_per_conversation": 0,
            "avg_conversation_length": 0,
        }

    def get_daily_range_lightweight(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> List[Dict]:
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
            WHERE {' AND '.join(conditions)}
        """

        return self.db.fetch_all(query, tuple(params))

    def get_batch_analysis_aggregates(
        self, start_date: str, end_date: str, host_name: Optional[str] = None
    ) -> Dict:
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

    def get_user_messages_stats(self, start_date: str, end_date: str, sender_prefix: str) -> Dict:
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
        result = self.db.fetch_one(query, (start_date, end_date, f"{sender_prefix}%"))

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
    ) -> List[Dict]:
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
        session_query = """
            SELECT DISTINCT COALESCE(agent_session_id, conversation_id) as session_id
            FROM daily_messages
            WHERE date >= ? AND date <= ?
              AND sender_name LIKE ?
              AND role = 'user'
              AND COALESCE(agent_session_id, conversation_id) IS NOT NULL
            LIMIT ?
        """
        sessions = self.db.fetch_all(
            session_query, (start_date, end_date, f"{sender_prefix}%", limit)
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
