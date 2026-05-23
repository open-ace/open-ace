"""Unit tests for Message, DailyMessage, and Conversation models."""

from datetime import datetime

import pytest

from app.models.message import Conversation, DailyMessage, Message


class TestMessage:
    """Test Message dataclass."""

    def test_create_with_required_fields(self):
        msg = Message(date="2025-01-01", tool_name="qwen", message_id="m1", role="user")
        assert msg.date == "2025-01-01"
        assert msg.tool_name == "qwen"
        assert msg.message_id == "m1"
        assert msg.role == "user"
        assert msg.host_name == "localhost"
        assert msg.parent_id is None
        assert msg.content is None
        assert msg.full_entry is None
        assert msg.tokens_used == 0
        assert msg.input_tokens == 0
        assert msg.output_tokens == 0
        assert msg.model is None
        assert msg.timestamp is None
        assert msg.sender_id is None
        assert msg.sender_name is None
        assert msg.message_source is None
        assert msg.feishu_conversation_id is None
        assert msg.group_subject is None
        assert msg.is_group_chat is None
        assert msg.id is None
        assert msg.created_at is None

    def test_create_with_all_fields(self):
        ts = datetime(2025, 6, 15, 10, 30, 0)
        ca = datetime(2025, 6, 15, 10, 30, 5)
        msg = Message(
            date="2025-06-15",
            tool_name="claude",
            message_id="msg-100",
            role="assistant",
            host_name="myhost",
            parent_id="msg-099",
            content="Hello world",
            full_entry="Full entry text",
            tokens_used=150,
            input_tokens=50,
            output_tokens=100,
            model="claude-sonnet-4-20250514",
            timestamp=ts,
            sender_id="user-1",
            sender_name="Alice",
            message_source="api",
            feishu_conversation_id="conv-123",
            group_subject="Project Discussion",
            is_group_chat=True,
            id=42,
            created_at=ca,
        )
        assert msg.host_name == "myhost"
        assert msg.parent_id == "msg-099"
        assert msg.content == "Hello world"
        assert msg.full_entry == "Full entry text"
        assert msg.tokens_used == 150
        assert msg.input_tokens == 50
        assert msg.output_tokens == 100
        assert msg.model == "claude-sonnet-4-20250514"
        assert msg.timestamp == ts
        assert msg.sender_id == "user-1"
        assert msg.sender_name == "Alice"
        assert msg.message_source == "api"
        assert msg.feishu_conversation_id == "conv-123"
        assert msg.group_subject == "Project Discussion"
        assert msg.is_group_chat is True
        assert msg.id == 42
        assert msg.created_at == ca

    def test_to_dict(self):
        ts = datetime(2025, 3, 10, 12, 0, 0)
        ca = datetime(2025, 3, 10, 12, 0, 1)
        msg = Message(
            date="2025-03-10",
            tool_name="qwen",
            message_id="m1",
            role="user",
            timestamp=ts,
            created_at=ca,
            id=1,
        )
        d = msg.to_dict()
        assert d["id"] == 1
        assert d["date"] == "2025-03-10"
        assert d["tool_name"] == "qwen"
        assert d["message_id"] == "m1"
        assert d["role"] == "user"
        assert d["host_name"] == "localhost"
        assert d["timestamp"] == "2025-03-10T12:00:00"
        assert d["created_at"] == "2025-03-10T12:00:01"
        assert d["parent_id"] is None
        assert d["content"] is None
        assert d["tokens_used"] == 0

    def test_to_dict_none_timestamps(self):
        msg = Message(date="2025-01-01", tool_name="qwen", message_id="m1", role="user")
        d = msg.to_dict()
        assert d["timestamp"] is None
        assert d["created_at"] is None

    def test_from_dict_basic(self):
        data = {
            "id": 10,
            "date": "2025-01-01",
            "tool_name": "claude",
            "message_id": "m10",
            "role": "assistant",
            "content": "Reply text",
        }
        msg = Message.from_dict(data)
        assert msg.id == 10
        assert msg.date == "2025-01-01"
        assert msg.tool_name == "claude"
        assert msg.message_id == "m10"
        assert msg.role == "assistant"
        assert msg.content == "Reply text"

    def test_from_dict_defaults(self):
        data = {}
        msg = Message.from_dict(data)
        assert msg.date == ""
        assert msg.tool_name == ""
        assert msg.message_id == ""
        assert msg.role == ""
        assert msg.host_name == "localhost"
        assert msg.tokens_used == 0
        assert msg.input_tokens == 0
        assert msg.output_tokens == 0

    def test_from_dict_timestamp_with_z_suffix(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "timestamp": "2025-01-15T10:30:00Z",
        }
        msg = Message.from_dict(data)
        assert msg.timestamp is not None
        assert msg.timestamp.year == 2025
        assert msg.timestamp.month == 1
        assert msg.timestamp.day == 15

    def test_from_dict_timestamp_as_datetime_object(self):
        ts = datetime(2025, 5, 20, 14, 30, 0)
        data = {
            "date": "2025-05-20",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "timestamp": ts,
        }
        msg = Message.from_dict(data)
        assert msg.timestamp == ts

    def test_from_dict_timestamp_invalid_string(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "timestamp": "not-a-valid-timestamp",
        }
        msg = Message.from_dict(data)
        assert msg.timestamp is None

    def test_from_dict_timestamp_none(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "timestamp": None,
        }
        msg = Message.from_dict(data)
        assert msg.timestamp is None

    def test_from_dict_is_group_chat_bool(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": True,
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is True

    def test_from_dict_is_group_chat_false_bool(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": False,
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is False

    def test_from_dict_is_group_chat_int_one(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": 1,
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is True

    def test_from_dict_is_group_chat_int_zero(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": 0,
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is False

    def test_from_dict_is_group_chat_string_true(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": "true",
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is True

    def test_from_dict_is_group_chat_string_yes(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": "yes",
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is True

    def test_from_dict_is_group_chat_string_one(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": "1",
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is True

    def test_from_dict_is_group_chat_string_false(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "is_group_chat": "false",
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is False

    def test_from_dict_is_group_chat_none(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
        }
        msg = Message.from_dict(data)
        assert msg.is_group_chat is None

    def test_from_dict_created_at_with_z_suffix(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "created_at": "2025-06-01T08:00:00Z",
        }
        msg = Message.from_dict(data)
        assert msg.created_at is not None
        assert msg.created_at.year == 2025
        assert msg.created_at.month == 6

    def test_from_dict_created_at_as_datetime(self):
        ca = datetime(2025, 7, 1, 9, 0, 0)
        data = {
            "date": "2025-07-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "created_at": ca,
        }
        msg = Message.from_dict(data)
        assert msg.created_at == ca

    def test_from_dict_created_at_invalid_string(self):
        data = {
            "date": "2025-01-01",
            "tool_name": "qwen",
            "message_id": "m1",
            "role": "user",
            "created_at": "invalid-date",
        }
        msg = Message.from_dict(data)
        assert msg.created_at is None

    def test_roundtrip_to_dict_from_dict(self):
        ts = datetime(2025, 8, 10, 15, 30, 0)
        ca = datetime(2025, 8, 10, 15, 30, 1)
        original = Message(
            date="2025-08-10",
            tool_name="openclaw",
            message_id="m-rt",
            role="user",
            host_name="server1",
            content="Test content",
            tokens_used=200,
            timestamp=ts,
            created_at=ca,
            id=99,
            is_group_chat=False,
        )
        d = original.to_dict()
        restored = Message.from_dict(d)
        assert restored.date == original.date
        assert restored.tool_name == original.tool_name
        assert restored.message_id == original.message_id
        assert restored.role == original.role
        assert restored.host_name == original.host_name
        assert restored.content == original.content
        assert restored.tokens_used == original.tokens_used
        assert restored.id == original.id


class TestDailyMessage:
    """Test DailyMessage dataclass."""

    def test_create_with_defaults(self):
        dm = DailyMessage(date="2025-01-01")
        assert dm.date == "2025-01-01"
        assert dm.total_messages == 0
        assert dm.total_tokens == 0
        assert dm.total_input_tokens == 0
        assert dm.total_output_tokens == 0
        assert dm.messages == []

    def test_create_with_values(self):
        msg = Message(date="2025-01-01", tool_name="qwen", message_id="m1", role="user")
        dm = DailyMessage(
            date="2025-01-01",
            total_messages=10,
            total_tokens=500,
            total_input_tokens=200,
            total_output_tokens=300,
            messages=[msg],
        )
        assert dm.total_messages == 10
        assert dm.total_tokens == 500
        assert dm.total_input_tokens == 200
        assert dm.total_output_tokens == 300
        assert len(dm.messages) == 1

    def test_to_dict(self):
        msg = Message(date="2025-01-01", tool_name="qwen", message_id="m1", role="user")
        dm = DailyMessage(
            date="2025-06-15",
            total_messages=5,
            total_tokens=100,
            total_input_tokens=40,
            total_output_tokens=60,
            messages=[msg],
        )
        d = dm.to_dict()
        assert d["date"] == "2025-06-15"
        assert d["total_messages"] == 5
        assert d["total_tokens"] == 100
        assert d["total_input_tokens"] == 40
        assert d["total_output_tokens"] == 60
        assert len(d["messages"]) == 1
        assert d["messages"][0]["message_id"] == "m1"

    def test_to_dict_empty_messages(self):
        dm = DailyMessage(date="2025-01-01")
        d = dm.to_dict()
        assert d["messages"] == []


class TestConversation:
    """Test Conversation dataclass."""

    def test_create_with_required_fields(self):
        conv = Conversation(session_id="s1", tool_name="qwen", host_name="localhost")
        assert conv.session_id == "s1"
        assert conv.tool_name == "qwen"
        assert conv.host_name == "localhost"
        assert conv.messages == []
        assert conv.total_tokens == 0
        assert conv.total_input_tokens == 0
        assert conv.total_output_tokens == 0
        assert conv.first_message_time is None
        assert conv.last_message_time is None
        assert conv.sender_name is None
        assert conv.sender_id is None

    def test_create_with_all_fields(self):
        msg = Message(date="2025-01-01", tool_name="qwen", message_id="m1", role="user")
        conv = Conversation(
            session_id="s2",
            tool_name="claude",
            host_name="myhost",
            messages=[msg],
            total_tokens=300,
            total_input_tokens=100,
            total_output_tokens=200,
            first_message_time="2025-01-01T10:00:00",
            last_message_time="2025-01-01T10:05:00",
            sender_name="Bob",
            sender_id="user-2",
        )
        assert conv.session_id == "s2"
        assert conv.tool_name == "claude"
        assert conv.host_name == "myhost"
        assert len(conv.messages) == 1
        assert conv.total_tokens == 300
        assert conv.first_message_time == "2025-01-01T10:00:00"
        assert conv.last_message_time == "2025-01-01T10:05:00"
        assert conv.sender_name == "Bob"
        assert conv.sender_id == "user-2"

    def test_to_dict(self):
        msg = Message(
            date="2025-01-01", tool_name="qwen", message_id="m1", role="user", content="Hi"
        )
        conv = Conversation(
            session_id="s3",
            tool_name="openclaw",
            host_name="server",
            messages=[msg],
            total_tokens=50,
            sender_name="Alice",
            sender_id="u1",
        )
        d = conv.to_dict()
        assert d["session_id"] == "s3"
        assert d["tool_name"] == "openclaw"
        assert d["host_name"] == "server"
        assert len(d["messages"]) == 1
        assert d["messages"][0]["content"] == "Hi"
        assert d["total_tokens"] == 50
        assert d["total_input_tokens"] == 0
        assert d["total_output_tokens"] == 0
        assert d["first_message_time"] is None
        assert d["last_message_time"] is None
        assert d["sender_name"] == "Alice"
        assert d["sender_id"] == "u1"
