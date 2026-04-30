#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Message Service

Business logic for message data operations.
"""

import logging
from datetime import datetime
from typing import Optional

from app.repositories.message_repo import MessageRepository
from app.utils.cache import cached

logger = logging.getLogger(__name__)


class MessageService:
    """Service for message-related business logic."""

    def __init__(self, message_repo: Optional[MessageRepository] = None):
        """
        Initialize service.

        Args:
            message_repo: Optional MessageRepository instance for dependency injection.
        """
        self.message_repo = message_repo or MessageRepository()

    def get_messages(
        self,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        sender_name: Optional[str] = None,
        role: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """
        Get messages with pagination.

        Args:
            date: Optional single date filter.
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            sender_name: Optional sender name filter.
            role: Optional role filter.
            search: Optional search term for content.
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            Dict: Messages and pagination info.
        """
        if date:
            messages = self.message_repo.get_messages_by_date(
                date=date,
                tool_name=tool_name,
                host_name=host_name,
                role=role,
                sender_name=sender_name,
                search=search,
                limit=limit,
                offset=offset,
            )
            total = self.message_repo.count_messages(
                start_date=date,
                end_date=date,
                tool_name=tool_name,
                host_name=host_name,
                sender_name=sender_name,
                role=role,
                search=search,
            )
        else:
            if not start_date:
                start_date = datetime.now().strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.now().strftime("%Y-%m-%d")

            messages = self.message_repo.get_messages_by_date_range(
                start_date=start_date,
                end_date=end_date,
                tool_name=tool_name,
                host_name=host_name,
                role=role,
                sender_name=sender_name,
                search=search,
                limit=limit,
                offset=offset,
            )
            total = self.message_repo.count_messages(
                start_date=start_date,
                end_date=end_date,
                tool_name=tool_name,
                host_name=host_name,
                sender_name=sender_name,
                role=role,
                search=search,
            )

        return {
            "messages": messages,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

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
        Get conversation history.

        Args:
            date: Optional date filter.
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            sender_name: Optional sender name filter.
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            List[Dict]: List of conversations.
        """
        return self.message_repo.get_conversation_history(
            date=date,
            start_date=start_date,
            end_date=end_date,
            tool_name=tool_name,
            host_name=host_name,
            sender_name=sender_name,
            limit=limit,
            offset=offset,
        )

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
        return self.message_repo.count_conversations(
            date=date,
            start_date=start_date,
            end_date=end_date,
            tool_name=tool_name,
            host_name=host_name,
            sender_name=sender_name,
        )

    def get_conversation_timeline(self, session_id: str) -> list[dict]:
        """
        Get timeline of messages for a conversation.

        Args:
            session_id: Conversation/session ID.

        Returns:
            List[Dict]: List of messages in the conversation.
        """
        return self.message_repo.get_conversation_timeline(session_id)

    def get_conversation_details(self, session_id: str) -> Optional[dict]:
        """
        Get details of a conversation.

        Args:
            session_id: Conversation/session ID.

        Returns:
            Optional[Dict]: Conversation details or None.
        """
        return self.message_repo.get_conversation_details(session_id)

    @cached(ttl=300, key_prefix="message", skip_args=[0])
    def get_all_senders(self, host_name: Optional[str] = None) -> list[str]:
        """
        Get list of all senders.

        Args:
            host_name: Optional host name filter.

        Returns:
            List[str]: List of sender names.
        """
        return self.message_repo.get_all_senders(host_name)

    def count_messages(
        self,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None,
        sender_name: Optional[str] = None,
        role: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """
        Count messages with filters.

        Args:
            date: Optional single date filter.
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            sender_name: Optional sender name filter.
            role: Optional role filter.
            search: Optional search term for content.

        Returns:
            int: Total count of messages.
        """
        if date:
            start_date = end_date = date
        return self.message_repo.count_messages(
            start_date=start_date,
            end_date=end_date,
            tool_name=tool_name,
            host_name=host_name,
            sender_name=sender_name,
            search=search,
        )

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
        Save a message.

        Returns:
            bool: True if successful.
        """
        return self.message_repo.save_message(
            date=date,
            tool_name=tool_name,
            message_id=message_id,
            role=role,
            host_name=host_name,
            parent_id=parent_id,
            content=content,
            full_entry=full_entry,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            timestamp=timestamp,
            sender_id=sender_id,
            sender_name=sender_name,
            message_source=message_source,
            feishu_conversation_id=feishu_conversation_id,
            group_subject=group_subject,
            is_group_chat=is_group_chat,
            agent_session_id=agent_session_id,
            conversation_id=conversation_id,
        )
