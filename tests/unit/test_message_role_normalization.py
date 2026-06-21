"""Unit tests for message-role normalization.

Guards the conversation-detail role-filter bug: tool-result messages persisted
under different spellings (``tool`` vs ``toolResult`` / ``tool_result``) must
all collapse to the canonical ``tool`` role at the write boundary, so the
conversation-detail role filter, message statistics and latency curve can never
again surface as "no messages found" for one of the write paths.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.message_repo import MessageRepository
from app.utils.roles import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    normalize_message_role,
)


class TestNormalizeMessageRole:
    """Pure unit tests for normalize_message_role."""

    @pytest.mark.parametrize(
        "variant",
        [
            "tool",
            "Tool",
            "TOOL",
            " tool ",
            "\ttool\n",
            "toolResult",
            "ToolResult",
            " toolResult ",
            "tool_result",
            "TOOL_RESULT",
        ],
    )
    def test_tool_result_spellings_collapse_to_tool(self, variant):
        assert normalize_message_role(variant) == "tool"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("user", "user"),
            ("assistant", "assistant"),
            ("system", "system"),
            ("USER", "user"),
            (" Assistant ", "assistant"),
        ],
    )
    def test_non_tool_roles_passthrough_lowercased(self, raw, expected):
        assert normalize_message_role(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_empty_returns_unknown(self, raw):
        assert normalize_message_role(raw) == "unknown"

    def test_unknown_role_lowercased_not_swallowed(self):
        # NEGATIVE: no prefix/fuzzy matching. An unknown role is preserved
        # (lower-cased), never force-merged into "tool".
        assert normalize_message_role("developer") == "developer"
        assert normalize_message_role("ToolUser") == "tooluser"

    def test_idempotent(self):
        once = normalize_message_role("toolResult")
        twice = normalize_message_role(once)
        assert once == twice == "tool"

    def test_role_constants(self):
        assert ROLE_USER == "user"
        assert ROLE_ASSISTANT == "assistant"
        assert ROLE_SYSTEM == "system"
        assert ROLE_TOOL == "tool"


class TestSaveMessageWriteBoundary:
    """save_message must normalize the role before writing daily_messages."""

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_tool_result_alias(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-21", tool_name="claude", message_id="m1", role="toolResult")
        params = db.execute.call_args[0][1]
        # role is the 6th positional column (date, tool_name, host_name,
        # message_id, parent_id, role) -> index 5.
        assert params[5] == "tool"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_tool_result_snake_case(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(
            date="2026-06-21", tool_name="claude", message_id="m2", role="tool_result"
        )
        params = db.execute.call_args[0][1]
        assert params[5] == "tool"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_preserves_canonical_tool(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-21", tool_name="claude", message_id="m3", role="tool")
        params = db.execute.call_args[0][1]
        assert params[5] == "tool"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_preserves_other_roles(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-21", tool_name="claude", message_id="m4", role="assistant")
        params = db.execute.call_args[0][1]
        assert params[5] == "assistant"
