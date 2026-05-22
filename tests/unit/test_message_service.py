"""Unit tests for MessageService."""

from unittest.mock import MagicMock

import pytest

from app.services.message_service import MessageService
from app.utils.cache import get_cache


class TestMessageService:
    """Test MessageService business logic."""

    def _make_service(self):
        mock_repo = MagicMock()
        svc = MessageService(message_repo=mock_repo)
        return svc, mock_repo

    def setup_method(self):
        get_cache().clear()

    def test_get_messages(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_messages.return_value = [{"id": 1, "content": "hello"}]
        mock_repo.count_messages.return_value = 1
        result = svc.get_messages(date="2026-01-01")
        assert "messages" in result
        assert "total" in result
        assert result["total"] == 1

    def test_get_messages_with_pagination(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_messages.return_value = [{"id": 1}]
        mock_repo.count_messages.return_value = 100
        result = svc.get_messages(
            start_date="2026-01-01", end_date="2026-01-31", limit=10, offset=0
        )
        assert result["limit"] == 10
        assert result["has_more"] is True

    def test_get_conversation_history(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_conversation_history.return_value = [
            {"session_id": "s1", "message_count": 5, "total_tokens": 100}
        ]
        result = svc.get_conversation_history(date="2026-01-01")
        assert len(result) == 1

    def test_count_conversations(self):
        svc, mock_repo = self._make_service()
        mock_repo.count_conversations.return_value = 42
        result = svc.count_conversations(date="2026-01-01")
        assert result == 42

    def test_get_conversation_timeline(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_conversation_timeline.return_value = [
            {"message_id": "m1", "role": "user"},
            {"message_id": "m2", "role": "assistant"},
        ]
        result = svc.get_conversation_timeline("session-1")
        assert len(result) == 2

    def test_get_conversation_details(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_conversation_details.return_value = {"session_id": "s1", "messages": 10}
        result = svc.get_conversation_details("session-1")
        assert result is not None

    def test_get_conversation_details_not_found(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_conversation_details.return_value = None
        result = svc.get_conversation_details("nonexistent")
        assert result is None

    def test_get_all_senders(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_all_senders.return_value = ["user1", "user2"]
        result = svc.get_all_senders()
        assert len(result) == 2

    def test_count_messages(self):
        svc, mock_repo = self._make_service()
        mock_repo.count_messages.return_value = 50
        result = svc.count_messages(date="2026-01-01")
        assert result == 50

    def test_save_message(self):
        svc, mock_repo = self._make_service()
        mock_repo.save_message.return_value = True
        result = svc.save_message(
            date="2026-01-01",
            tool_name="qwen",
            message_id="msg_1",
            role="user",
            content="Hello",
            tokens_used=10,
        )
        assert result is True

    def test_save_message_full_params(self):
        svc, mock_repo = self._make_service()
        mock_repo.save_message.return_value = True
        result = svc.save_message(
            date="2026-01-01",
            tool_name="qwen",
            message_id="msg_1",
            role="user",
            content="Hello",
            tokens_used=10,
            input_tokens=8,
            output_tokens=2,
            model="qwen-max",
            sender_name="admin",
        )
        assert result is True
