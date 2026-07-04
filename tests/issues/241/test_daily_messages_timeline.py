#!/usr/bin/env python3
"""
Tests for Issue #241 (#22): daily_messages conversation timeline pagination.

``MessageRepository.get_conversation_timeline`` previously SELECTed ``full_entry``
(the entire raw message JSON — hundreds of KB per row) for every row in a
conversation with no LIMIT, serializing multi-MB responses for long timelines.
This change drops ``full_entry`` and applies ``LIMIT ? OFFSET ?``.

These tests assert the query SHAPE (column list, LIMIT/OFFSET) against a mocked
``db.fetch_all`` — matching the convention in tests/unit/test_message_repo.py.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.repositories.message_repo import MessageRepository  # noqa: E402


def _make_repo(fetch_all_rows):
    mock_db = MagicMock()
    mock_db.fetch_all.return_value = fetch_all_rows
    return MessageRepository(db=mock_db), mock_db


class TestConversationTimelineShape:
    def test_does_not_select_full_entry(self):
        """``full_entry`` is a per-row size bomb and must never be in the SELECT."""
        repo, mock_db = _make_repo([])
        repo.get_conversation_timeline("conv-1", limit=50)

        query = mock_db.fetch_all.call_args[0][0]
        # Column allowlist is present...
        assert "content" in query
        assert "tokens_used" in query
        # ...but the heavy column is gone.
        assert (
            "full_entry" not in query
        ), "full_entry must be dropped from the timeline SELECT (size bomb)"

    def test_limit_offset_appended_when_limit_given(self):
        repo, mock_db = _make_repo([])
        repo.get_conversation_timeline("conv-1", limit=100, offset=200)

        query, params = mock_db.fetch_all.call_args[0]
        assert "LIMIT ? OFFSET ?" in query
        # params = (session_id, limit, offset)
        assert params == ("conv-1", 100, 200)

    def test_no_limit_keeps_unbounded_for_internal_callers(self):
        """``limit=None`` must preserve the legacy unbounded behavior for internal
        callers that still request the full timeline."""
        repo, mock_db = _make_repo([])
        repo.get_conversation_timeline("conv-1", limit=None)

        query, params = mock_db.fetch_all.call_args[0]
        assert "LIMIT" not in query
        assert params == ("conv-1",)

    def test_default_offset_is_zero(self):
        repo, mock_db = _make_repo([])
        repo.get_conversation_timeline("conv-1", limit=10)

        _, params = mock_db.fetch_all.call_args[0]
        # offset defaults to 0
        assert params == ("conv-1", 10, 0)

    def test_orders_by_timestamp_ascending(self):
        repo, mock_db = _make_repo([])
        repo.get_conversation_timeline("conv-1", limit=10)
        query = mock_db.fetch_all.call_args[0][0]
        assert "ORDER BY timestamp ASC" in query

    def test_result_passthrough(self):
        rows = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        repo, _ = _make_repo(rows)
        assert repo.get_conversation_timeline("conv-1", limit=10) == rows


class TestTimelineRouteDefaults:
    """The /conversation-timeline/<id> route caps the page (default 100, max 500)."""

    def test_route_clamps_limit_and_drops_full_entry(self, monkeypatch):
        from app.routes import messages as messages_route

        captured = {}

        def _fake_timeline(session_id, limit=None, offset=0):
            captured["limit"] = limit
            captured["offset"] = offset
            return [{"role": "user", "content": "x"}]

        monkeypatch.setattr(
            messages_route.message_service, "get_conversation_timeline", _fake_timeline
        )

        from flask import Flask

        app = Flask(__name__)

        # No limit param -> default 100.
        with app.test_request_context("/api/messages/conversation-timeline/s1"):
            messages_route.api_conversation_timeline("s1")
        assert captured["limit"] == 100

        # limit=0 / negative -> default 100.
        with app.test_request_context("/api/messages/conversation-timeline/s1?limit=0"):
            messages_route.api_conversation_timeline("s1")
        assert captured["limit"] == 100

        # limit above MAX -> clamped to 500.
        with app.test_request_context("/api/messages/conversation-timeline/s1?limit=99999"):
            messages_route.api_conversation_timeline("s1")
        assert captured["limit"] == 500

        # Explicit small limit honored.
        with app.test_request_context("/api/messages/conversation-timeline/s1?limit=20&offset=40"):
            messages_route.api_conversation_timeline("s1")
        assert captured["limit"] == 20
        assert captured["offset"] == 40
