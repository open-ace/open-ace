"""Tests for Issue #3336: LLM Proxy message dedup via external_message_id."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.session_manager import SessionManager
from app.modules.workspace.usage_sink import _record_messages_internal

pytestmark = [pytest.mark.regression, pytest.mark.issue(3336)]


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    """Create a SessionManager with SQLite database for testing."""
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "test_3336.db"))
    sm._ensure_tables()
    conn = sm._get_connection()
    cur = conn.cursor()
    # Add optional columns that may be needed
    for col in ("project_id", "project_path"):
        try:
            cur.execute(f"ALTER TABLE agent_sessions ADD COLUMN {col} TEXT")
        except Exception:
            pass  # allow-swallow: idempotent ALTER for optional columns
    conn.commit()
    conn.close()
    return sm


class TestRecordMessagesInternalDedup:
    """Test that _record_messages_internal deduplicates user messages."""

    def _create_session(self, sm, session_id: str):
        """Create a test session with a unique ID."""
        sm.create_session(
            tool_name="test-tool",
            user_id=1,
            tenant_id=1,
            session_id=session_id,
        )

    def _make_request_body(self, messages: list[dict]) -> bytes:
        """Create a request body with messages."""
        return json.dumps({"messages": messages}).encode("utf-8")

    def _make_response_body(self, content: str = "response") -> bytes:
        """Create a response body with assistant message."""
        return json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode("utf-8")

    def _count_user_messages(self, sm, session_id: str) -> int:
        """Count user messages for a session."""
        conn = sm._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ? AND role = ?",
            (session_id, "user"),
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def test_tool_continuation_same_user_message_dedups(self, sqlite_sm):
        """Issue #3336: Tool continuation should not duplicate user message."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        # First request: user asks a question
        messages_1 = [
            {"role": "user", "content": "What is 2+2?"},
        ]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_1),
            response_body=self._make_response_body("Let me calculate."),
            output_tokens=10,
            model="test-model",
        )

        # Second request: tool continuation, same user message
        messages_2 = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "Let me calculate."},
            {"role": "tool", "content": "result: 4"},
        ]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_2),
            response_body=self._make_response_body("The answer is 4."),
            output_tokens=10,
            model="test-model",
        )

        # Verify only one user message was stored
        user_count = self._count_user_messages(sqlite_sm, session_id)
        assert user_count == 1, f"Expected 1 user message, got {user_count}"

    def test_tool_continuation_multiple_times_dedups(self, sqlite_sm):
        """Issue #3336: Multiple tool continuations should not duplicate user message."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        # Simulate 6 tool continuations (as described in the issue)
        user_content = "Please analyze this file"
        for i in range(6):
            messages = [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": f"Step {i}"},
                {"role": "tool", "content": f"Tool result {i}"},
            ]
            _record_messages_internal(
                sm=sqlite_sm,
                session_id=session_id,
                request_body=self._make_request_body(messages),
                response_body=self._make_response_body(f"Response {i}"),
                output_tokens=10,
                model="test-model",
            )

        # Verify only one user message was stored
        user_count = self._count_user_messages(sqlite_sm, session_id)
        assert user_count == 1, f"Expected 1 user message after 6 continuations, got {user_count}"

    def test_different_user_messages_are_separate(self, sqlite_sm):
        """Different user messages should be stored separately."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        # First request
        messages_1 = [{"role": "user", "content": "Hello"}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_1),
            response_body=self._make_response_body("Hi there!"),
            output_tokens=10,
            model="test-model",
        )

        # Second request: different user message
        messages_2 = [{"role": "user", "content": "Goodbye"}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_2),
            response_body=self._make_response_body("See you!"),
            output_tokens=10,
            model="test-model",
        )

        # Verify two user messages were stored
        user_count = self._count_user_messages(sqlite_sm, session_id)
        assert user_count == 2, f"Expected 2 different user messages, got {user_count}"

    def test_message_with_id_uses_id_for_dedup(self, sqlite_sm):
        """Messages with 'id' field should use it for dedup."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        msg_id = "unique-msg-12345"

        # First request with message ID
        messages_1 = [{"role": "user", "content": "Question", "id": msg_id}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_1),
            response_body=self._make_response_body("Answer"),
            output_tokens=10,
            model="test-model",
        )

        # Second request with same ID but different content
        messages_2 = [{"role": "user", "content": "Different content", "id": msg_id}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_2),
            response_body=self._make_response_body("Response"),
            output_tokens=10,
            model="test-model",
        )

        # Verify only one message (dedup by ID, not content)
        user_count = self._count_user_messages(sqlite_sm, session_id)
        assert user_count == 1, f"Expected 1 message (dedup by ID), got {user_count}"

    def test_message_without_id_uses_content_hash(self, sqlite_sm):
        """Messages without 'id' should use content hash for dedup."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        content = "This is a test question"

        # First request
        messages_1 = [{"role": "user", "content": content}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_1),
            response_body=self._make_response_body("Answer"),
            output_tokens=10,
            model="test-model",
        )

        # Second request: same content (should dedup via hash)
        messages_2 = [{"role": "user", "content": content}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_2),
            response_body=self._make_response_body("Response"),
            output_tokens=10,
            model="test-model",
        )

        # Verify only one message
        user_count = self._count_user_messages(sqlite_sm, session_id)
        assert user_count == 1, f"Expected 1 message (dedup by content hash), got {user_count}"

        # Verify external_message_id is set
        conn = sqlite_sm._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT external_message_id FROM session_messages WHERE session_id = ? AND role = ?",
            (session_id, "user"),
        )
        row = cursor.fetchone()
        conn.close()

        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        expected_id = f"llm_proxy:{expected_hash}"
        assert row[0] == expected_id, f"Expected external_message_id={expected_id}, got {row[0]}"

    def test_external_message_id_format(self, sqlite_sm):
        """Verify external_message_id format for messages with and without ID."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        self._create_session(sqlite_sm, session_id)

        # Message with ID
        msg_id = "msg-test-001"
        messages_1 = [{"role": "user", "content": "Question 1", "id": msg_id}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_1),
            response_body=self._make_response_body("Answer 1"),
            output_tokens=10,
            model="test-model",
        )

        # Message without ID
        content_2 = "Question 2"
        messages_2 = [{"role": "user", "content": content_2}]
        _record_messages_internal(
            sm=sqlite_sm,
            session_id=session_id,
            request_body=self._make_request_body(messages_2),
            response_body=self._make_response_body("Answer 2"),
            output_tokens=10,
            model="test-model",
        )

        # Check external_message_id values
        conn = sqlite_sm._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content, external_message_id FROM session_messages "
            "WHERE session_id = ? AND role = ? ORDER BY id",
            (session_id, "user"),
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 2

        # First message should have the provided ID
        assert rows[0][0] == "Question 1"
        assert rows[0][1] == msg_id

        # Second message should have content hash
        expected_hash = hashlib.sha256(content_2.encode("utf-8")).hexdigest()[:16]
        expected_id = f"llm_proxy:{expected_hash}"
        assert rows[1][0] == content_2
        assert rows[1][1] == expected_id
