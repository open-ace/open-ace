"""
Open ACE - Message Models

Data models for message tracking.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Message data model."""

    date: str
    tool_name: str
    message_id: str
    role: str
    host_name: str = "localhost"
    parent_id: Optional[str] = None
    content: Optional[str] = None
    full_entry: Optional[str] = None
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: Optional[str] = None
    timestamp: Optional[datetime] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    message_source: Optional[str] = None
    feishu_conversation_id: Optional[str] = None
    group_subject: Optional[str] = None
    is_group_chat: Optional[bool] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "date": self.date,
            "tool_name": self.tool_name,
            "host_name": self.host_name,
            "message_id": self.message_id,
            "parent_id": self.parent_id,
            "role": self.role,
            "content": self.content,
            "full_entry": self.full_entry,
            "tokens_used": self.tokens_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "message_source": self.message_source,
            "feishu_conversation_id": self.feishu_conversation_id,
            "group_subject": self.group_subject,
            "is_group_chat": self.is_group_chat,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """Create from dictionary."""
        # Extract values once to avoid repeated get() calls
        ts_value = data.get("timestamp")
        gc_value = data.get("is_group_chat")
        created_at_value = data.get("created_at")

        # Handle timestamp conversion from string to datetime
        timestamp = None
        if ts_value:
            if isinstance(ts_value, str):
                try:
                    timestamp = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse timestamp '{ts_value}': {e}")
                    timestamp = None
            elif isinstance(ts_value, datetime):
                timestamp = ts_value

        # Handle is_group_chat conversion (bool, int, or string)
        is_group_chat = None
        if gc_value is not None:
            if isinstance(gc_value, bool):
                is_group_chat = gc_value
            elif isinstance(gc_value, int):
                is_group_chat = gc_value == 1
            elif isinstance(gc_value, str):
                is_group_chat = gc_value.lower() in ("true", "1", "yes")

        # Handle created_at conversion
        created_at = None
        if created_at_value:
            if isinstance(created_at_value, str):
                try:
                    created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse created_at '{created_at_value}': {e}")
            elif isinstance(created_at_value, datetime):
                created_at = created_at_value

        return cls(
            id=data.get("id"),
            date=data.get("date"),
            tool_name=data.get("tool_name"),
            host_name=data.get("host_name", "localhost"),
            message_id=data.get("message_id"),
            parent_id=data.get("parent_id"),
            role=data.get("role"),
            content=data.get("content"),
            full_entry=data.get("full_entry"),
            tokens_used=data.get("tokens_used", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            model=data.get("model"),
            timestamp=timestamp,
            sender_id=data.get("sender_id"),
            sender_name=data.get("sender_name"),
            message_source=data.get("message_source"),
            feishu_conversation_id=data.get("feishu_conversation_id"),
            group_subject=data.get("group_subject"),
            is_group_chat=is_group_chat,
            created_at=created_at,
        )


@dataclass
class DailyMessage:
    """Aggregated daily message data."""

    date: str
    total_messages: int = 0
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    messages: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "date": self.date,
            "total_messages": self.total_messages,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "messages": [m.to_dict() for m in self.messages],
        }


@dataclass
class Conversation:
    """Conversation data model for grouping messages."""

    session_id: str
    tool_name: str
    host_name: str
    messages: list[Message] = field(default_factory=list)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    first_message_time: Optional[str] = None
    last_message_time: Optional[str] = None
    sender_name: Optional[str] = None
    sender_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "host_name": self.host_name,
            "messages": [m.to_dict() for m in self.messages],
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "first_message_time": self.first_message_time,
            "last_message_time": self.last_message_time,
            "sender_name": self.sender_name,
            "sender_id": self.sender_id,
        }
