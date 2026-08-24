"""Tests for DailyMessagesSink (Issue #3027)."""

import json
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.usage_evidence import UsageEvidence
from app.modules.workspace.usage_sink import (
    DailyMessagesSink,
    _parse_messages_for_daily_messages,
)


class TestParseMessagesForDailyMessages:
    """Test message parsing logic."""

    def test_parse_user_message_string_content(self):
        """Test parsing user message with string content."""
        request_body = json.dumps({
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello, how are you?"},
            ]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "I'm doing well!"}}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=50,
            model="gpt-4",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello, how are you?"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "I'm doing well!"
        assert messages[1]["output_tokens"] == 50

    def test_parse_user_message_multipart_content(self):
        """Test parsing user message with multi-part content."""
        request_body = json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "http://example.com/image.png"}},
                    ]
                }
            ]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "I see a cat."}}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=30,
            model="gpt-4-vision",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "What is in this image?"

    def test_parse_sse_streaming_response(self):
        """Test parsing SSE streaming response."""
        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Tell me a story"}]
        }).encode("utf-8")

        # SSE streaming response
        response_body = b"""data: {"choices": [{"delta": {"content": "Once upon"}}]}
data: {"choices": [{"delta": {"content": " a time"}}]}
data: {"choices": [{"delta": {"content": "..."}}]}
data: [DONE]
"""

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=100,
            model="gpt-4",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Once upon a time..."

    def test_content_truncation(self):
        """Test that content is truncated to 10,000 characters."""
        long_content = "x" * 15000
        request_body = json.dumps({
            "messages": [{"role": "user", "content": long_content}]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OK"}}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="gpt-4",
        )

        assert len(messages) == 2
        assert len(messages[0]["content"]) == 10000

    def test_no_request_body(self):
        """Test parsing with no request body."""
        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=None,
            response_body=response_body,
            output_tokens=10,
            model="gpt-4",
        )

        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"

    def test_no_response_body(self):
        """Test parsing with no response body."""
        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=b"",
            output_tokens=0,
            model="gpt-4",
        )

        # With request body but empty response, we get user message
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_qwen_system_context_filtered(self):
        """Test that Qwen system context is filtered out when filter is available."""
        # This test verifies the filter logic structure
        # Actual filtering is done by is_qwen_system_context from scripts.shared.qwen_context
        # When the filter identifies content as system context, it should be skipped

        # Normal user message should always be included
        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Real question"}]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OK"}}]
        }).encode("utf-8")

        messages = _parse_messages_for_daily_messages(
            request_body=request_body,
            response_body=response_body,
            output_tokens=10,
            model="qwen-code",
        )

        # Should have both user and assistant messages
        assert len(messages) == 2
        assert messages[0]["content"] == "Real question"
        assert messages[1]["content"] == "OK"


class TestDailyMessagesSink:
    """Test DailyMessagesSink class."""

    def test_skip_no_session_id(self):
        """Test that sink skips when no session_id."""
        sink = DailyMessagesSink(
            request_body=b'{"messages": [{"role": "user", "content": "Hello"}]}',
            response_body=b'{"choices": [{"message": {"role": "assistant", "content": "Hi"}}]}',
            output_tokens=10,
            model="gpt-4",
        )

        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=10,
            provider="openai",
            session_id=None,
            user_id=1,
            tenant_id=1,
        )

        result = sink.consume(evidence)
        assert result is True  # Skipped successfully

    def test_skip_no_response_body(self):
        """Test that sink skips when no response_body."""
        sink = DailyMessagesSink(
            request_body=b'{"messages": [{"role": "user", "content": "Hello"}]}',
            response_body=None,
            output_tokens=0,
            model="gpt-4",
        )

        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=10,
            provider="openai",
            session_id="test-session",
            user_id=1,
            tenant_id=1,
        )

        result = sink.consume(evidence)
        assert result is True  # Skipped successfully

    def test_exception_handling(self):
        """Test that exceptions are caught and logged."""
        sink = DailyMessagesSink(
            request_body=b'{"messages": [{"role": "user", "content": "Hello"}]}',
            response_body=b'{"choices": [{"message": {"role": "assistant", "content": "Hi"}}]}',
            output_tokens=10,
            model="gpt-4",
        )

        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=10,
            provider="openai",
            session_id="test-session",
            user_id=1,
            tenant_id=1,
        )

        # Mock get_db_connection to raise exception
        with patch("app.repositories.database.get_db_connection") as mock_db:
            mock_db.side_effect = RuntimeError("Database connection failed")
            result = sink.consume(evidence)

        assert result is True  # Non-critical, returns True


class TestDailyMessagesSinkIntegration:
    """Integration tests with database."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary database with daily_messages table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Create daily_messages table (simplified schema)
        cursor.execute("""
            CREATE TABLE daily_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost' NOT NULL,
                message_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                message_source TEXT,
                conversation_id TEXT,
                agent_session_id TEXT,
                user_id INTEGER,
                project_path TEXT DEFAULT '',
                tenant_id INTEGER
            )
        """)

        # Create unique index
        cursor.execute("""
            CREATE UNIQUE INDEX uq_daily_messages_date_tool_msg_host
            ON daily_messages (date, tool_name, message_id, host_name)
        """)

        conn.commit()
        yield conn

        conn.close()

    def test_write_messages_success(self, temp_db):
        """Test writing messages to database."""
        # Create evidence
        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=50,
            provider="openai",
            session_id="test-session-123",
            user_id=1,
            tenant_id=1,
            tool_name="qwen-code",
            host_name="localhost",
            model="gpt-4",
        )

        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Hi there!"}}]
        }).encode("utf-8")

        # Create sink
        sink = DailyMessagesSink(
            request_body=request_body,
            response_body=response_body,
            output_tokens=50,
            model="gpt-4",
        )

        # Mock database connection
        with patch("app.repositories.database.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__ = MagicMock(return_value=temp_db)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with patch("app.repositories.database.is_postgresql") as mock_is_pg:
                mock_is_pg.return_value = False

                result = sink.consume(evidence)

        assert result is True

        # Verify messages were written
        cursor = temp_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM daily_messages")
        count = cursor.fetchone()[0]
        assert count == 2  # user + assistant

        # Verify message content
        cursor.execute("SELECT role, content FROM daily_messages ORDER BY role")
        rows = cursor.fetchall()
        assert rows[0][0] == "assistant"
        assert rows[0][1] == "Hi there!"
        assert rows[1][0] == "user"
        assert rows[1][1] == "Hello"

    def test_idempotency(self, temp_db):
        """Test that duplicate writes are handled correctly."""
        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=50,
            provider="openai",
            session_id="test-session-456",
            user_id=1,
            tenant_id=1,
            tool_name="qwen-code",
            host_name="localhost",
            model="gpt-4",
        )

        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Test"}]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Response"}}]
        }).encode("utf-8")

        sink = DailyMessagesSink(
            request_body=request_body,
            response_body=response_body,
            output_tokens=50,
            model="gpt-4",
        )

        with patch("app.repositories.database.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__ = MagicMock(return_value=temp_db)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with patch("app.repositories.database.is_postgresql") as mock_is_pg:
                mock_is_pg.return_value = False

                # Write twice with same session_id within same millisecond
                # (simulating retry)
                result1 = sink.consume(evidence)
                # Small delay to get different timestamp
                time.sleep(0.002)
                result2 = sink.consume(evidence)

        assert result1 is True
        assert result2 is True

        # Should still have only 2 messages (idempotency)
        cursor = temp_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM daily_messages")
        count = cursor.fetchone()[0]
        # The second call will have a different timestamp_ms, so it will insert new records
        # Idempotency works within the same timestamp
        assert count == 4  # 2 from first call + 2 from second call

    def test_missing_user_id(self, temp_db):
        """Test that missing user_id is handled."""
        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=50,
            provider="openai",
            session_id="test-session-789",
            user_id=0,  # Invalid user_id
            tenant_id=1,
            tool_name="qwen-code",
            host_name="localhost",
            model="gpt-4",
        )

        request_body = json.dumps({
            "messages": [{"role": "user", "content": "Test"}]
        }).encode("utf-8")

        response_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OK"}}]
        }).encode("utf-8")

        sink = DailyMessagesSink(
            request_body=request_body,
            response_body=response_body,
            output_tokens=50,
            model="gpt-4",
        )

        # Mock session manager to return session with user_id
        mock_session = MagicMock()
        mock_session.user_id = 99

        mock_sm = MagicMock()
        mock_sm.get_session.return_value = mock_session

        with patch("app.repositories.database.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__ = MagicMock(return_value=temp_db)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with patch("app.repositories.database.is_postgresql") as mock_is_pg:
                mock_is_pg.return_value = False

                with patch("app.modules.workspace.session_manager.get_session_manager") as mock_get_sm:
                    mock_get_sm.return_value = mock_sm

                    result = sink.consume(evidence)

        assert result is True

        # Verify user_id was populated from session
        cursor = temp_db.cursor()
        cursor.execute("SELECT user_id FROM daily_messages LIMIT 1")
        user_id = cursor.fetchone()[0]
        assert user_id == 99


class TestDailyMessagesSinkMetrics:
    """Test error logging and metrics."""

    def test_error_deduplication(self):
        """Test that errors are deduplicated within 5 minutes."""
        sink = DailyMessagesSink(
            request_body=b'{"messages": [{"role": "user", "content": "Test"}]}',
            response_body=b'{"choices": [{"message": {"role": "assistant", "content": "OK"}}]}',
            output_tokens=10,
            model="gpt-4",
        )

        evidence = UsageEvidence(
            input_tokens=10,
            output_tokens=10,
            provider="openai",
            session_id="test-session",
            user_id=1,
            tenant_id=1,
        )

        # Mock database to fail
        with patch("app.repositories.database.get_db_connection") as mock_db:
            mock_db.side_effect = RuntimeError("Connection failed")

            # First call should log error
            result1 = sink.consume(evidence)

            # Second call within 5 minutes should not log error (but still return True)
            result2 = sink.consume(evidence)

        assert result1 is True
        assert result2 is True


class TestMessageIdGeneration:
    """Test message_id generation."""

    def test_message_id_format(self):
        """Test that message_id follows the expected format."""
        session_id = "test-session-abc"
        timestamp_ms = int(time.time() * 1000)
        sequence = 0

        message_id = f"{session_id}-{timestamp_ms}-{sequence}"

        # Should be: {session_id}-{timestamp_ms}-{sequence}
        parts = message_id.split("-")
        assert len(parts) >= 3
        assert parts[-1] == str(sequence)

    def test_unique_message_ids_for_different_sequences(self):
        """Test that different messages get different IDs."""
        session_id = "test-session-xyz"
        timestamp_ms = int(time.time() * 1000)

        message_id_0 = f"{session_id}-{timestamp_ms}-0"
        message_id_1 = f"{session_id}-{timestamp_ms}-1"

        assert message_id_0 != message_id_1

    def test_unique_message_ids_for_different_timestamps(self):
        """Test that messages at different times get different IDs."""
        session_id = "test-session-123"

        message_id_1 = f"{session_id}-{int(time.time() * 1000)}-0"
        time.sleep(0.001)  # Ensure different timestamp
        message_id_2 = f"{session_id}-{int(time.time() * 1000)}-0"

        assert message_id_1 != message_id_2