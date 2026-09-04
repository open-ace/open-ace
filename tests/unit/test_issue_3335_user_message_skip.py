"""
Unit tests for Issue #3335: Last user message skipped when it's Qwen system context.

Tests that the message recording functions properly skip Qwen system context
messages and continue searching for real user messages.
"""

import json

import pytest


class MockSessionManager:
    """Mock session manager for testing."""

    def __init__(self):
        self.messages = []

    def append_transcript_message(
        self, session_id, role, content, source, tokens_used=None, model=None
    ):
        class StoredMessage:
            _was_inserted = True

        self.messages.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "source": source,
            }
        )
        return StoredMessage()


class TestRecordMessagesInternalIssue3335:
    """Test _record_messages_internal for Issue #3335 fix."""

    def test_real_user_followed_by_startup_context(self):
        """Real user message followed by Qwen startup context should record real user."""
        from app.modules.workspace.usage_sink import _record_messages_internal

        sm = MockSessionManager()

        # Request with real user message, then Qwen startup context
        request_body = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is 2+2?"},
                    {
                        "role": "user",
                        "content": "This is the Qwen Code. We are setting up the context for our chat.",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "2+2 equals 4."}}]}
        ).encode()

        delta = _record_messages_internal(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should have recorded both user and assistant messages
        assert delta == 2, f"Expected 2 messages, got {delta}"
        assert len(sm.messages) == 2

        # First recorded message should be the real user message
        user_msg = [m for m in sm.messages if m["role"] == "user"][0]
        assert user_msg["content"] == "What is 2+2?", f"Got: {user_msg['content']}"

    def test_multiple_system_contexts_at_end(self):
        """Multiple Qwen system contexts at end should skip all and find real user."""
        from app.modules.workspace.usage_sink import _record_messages_internal

        sm = MockSessionManager()

        # Request with multiple system context messages at the end
        request_body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Help me with Python"},
                    {"role": "assistant", "content": "Sure, what do you need?"},
                    {
                        "role": "user",
                        "content": "[Platform Tool Limits]\n- Tool: bash",
                    },
                    {
                        "role": "user",
                        "content": "Memory directory: /home/user/.qwen/memories",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Python help"}}]}
        ).encode()

        delta = _record_messages_internal(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should have recorded the real user message
        assert delta == 2, f"Expected 2 messages, got {delta}"

        user_msg = [m for m in sm.messages if m["role"] == "user"][0]
        assert user_msg["content"] == "Help me with Python"

    def test_only_system_context_no_real_user(self):
        """Only system context with no real user should not record any user message."""
        from app.modules.workspace.usage_sink import _record_messages_internal

        sm = MockSessionManager()

        # Request with only system context
        request_body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "This is the Qwen Code. We are setting up the context for our chat.",
                    },
                    {"role": "user", "content": "[Platform Tool Limits]"},
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Response"}}]}
        ).encode()

        delta = _record_messages_internal(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should only have assistant message, no user
        assert delta == 1, f"Expected 1 message (assistant only), got {delta}"
        user_msgs = [m for m in sm.messages if m["role"] == "user"]
        assert len(user_msgs) == 0, "Should not have recorded any user message"

    def test_multipart_content_with_system_context(self):
        """Multipart user content should work the same as string content."""
        from app.modules.workspace.usage_sink import _record_messages_internal

        sm = MockSessionManager()

        # Request with multipart content
        request_body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze this code"},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "This is the Qwen Code. We are setting up the context for our chat.",
                            }
                        ],
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Analysis"}}]}
        ).encode()

        delta = _record_messages_internal(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        assert delta == 2
        user_msg = [m for m in sm.messages if m["role"] == "user"][0]
        assert user_msg["content"] == "Analyze this code"


class TestParseMessagesForDailyMessagesIssue3335:
    """Test _parse_messages_for_daily_messages for Issue #3335 fix."""

    def test_real_user_followed_by_startup_context(self):
        """Real user message followed by Qwen startup context should record real user."""
        from app.modules.workspace.usage_sink import _parse_messages_for_daily_messages

        # Request with real user message, then Qwen startup context
        request_body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "What is the weather?"},
                    {
                        "role": "user",
                        "content": "This is the Qwen Code. We are setting up the context for our chat.",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Weather info"}}]}
        ).encode()

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should have user and assistant messages
        assert len(messages) == 2

        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert user_msg["content"] == "What is the weather?"

    def test_session_and_daily_messages_consistency(self):
        """session_messages and daily_messages should have same user message."""
        from app.modules.workspace.usage_sink import (
            _parse_messages_for_daily_messages,
            _record_messages_internal,
        )

        sm = MockSessionManager()

        # Same request body for both
        request_body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Real question"},
                    {
                        "role": "user",
                        "content": "[Platform Tool Limits]\nTool: bash",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Answer"}}]}
        ).encode()

        # Record to session_messages
        _record_messages_internal(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Parse for daily_messages
        daily_messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Both should have the same user content
        session_user = [m for m in sm.messages if m["role"] == "user"][0]
        daily_user = [m for m in daily_messages if m["role"] == "user"][0]

        assert session_user["content"] == daily_user["content"] == "Real question"


class TestLlmProxyHandlerRecordMessagesIssue3335:
    """Test _record_messages in llm_proxy_handler for Issue #3335 fix."""

    def test_real_user_followed_by_startup_context(self):
        """Real user message followed by Qwen startup context should record real user."""
        from app.modules.workspace.llm_proxy_handler import _record_messages

        sm = MockSessionManager()

        # Request with real user message, then Qwen startup context
        request_body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Fix this bug"},
                    {
                        "role": "user",
                        "content": "This is the Qwen Code. We are setting up the context for our chat.",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Bug fixed"}}]}
        ).encode()

        delta = _record_messages(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should have recorded both user and assistant messages
        assert delta == 2, f"Expected 2 messages, got {delta}"

        user_msg = [m for m in sm.messages if m["role"] == "user"][0]
        assert user_msg["content"] == "Fix this bug"

    def test_filters_qwen_system_context(self):
        """Should filter out Qwen system context messages."""
        from app.modules.workspace.llm_proxy_handler import _record_messages

        sm = MockSessionManager()

        # Request with only system context
        request_body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "This is the Qwen Code. We are setting up the context for our chat.",
                    },
                ]
            }
        ).encode()

        response_body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "Response"}}]}
        ).encode()

        delta = _record_messages(
            sm=sm,
            session_id="test-session",
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="test-model",
        )

        # Should only have assistant message
        assert delta == 1
        user_msgs = [m for m in sm.messages if m["role"] == "user"]
        assert len(user_msgs) == 0, "Should not have recorded system context as user"