"""Unit tests for message-role normalization.

Guards the conversation-detail "ToolResult" filter bug: tool-execution results
must always be persisted with the canonical ``toolResult`` role regardless of
which write path emitted them. Different sources spell the role differently
(``toolResult`` from the OpenClaw importer, ``tool_result`` from the remote
agent session_sync, ``ToolResult`` casing drift). ``normalize_message_role``
collapses every variant at the write boundary so the frontend filter
(``msg.role === 'toolResult'``) matches consistently.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.message_repo import MessageRepository
from app.utils.tool_names import normalize_message_role


class TestNormalizeMessageRole:
    """Pure unit tests for normalize_message_role."""

    @pytest.mark.parametrize(
        "variant", ["toolResult", "ToolResult", "TOOLRESULT", " toolResult ", "toolResult\n"]
    )
    def test_canonical_tool_result_passthrough_and_case(self, variant):
        assert normalize_message_role(variant) == "toolResult"

    @pytest.mark.parametrize(
        "variant", ["tool_result", "Tool_Result", " TOOL_RESULT ", "tool-result", "Tool-Result"]
    )
    def test_tool_result_aliases_collapse(self, variant):
        assert normalize_message_role(variant) == "toolResult"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("user", "user"),
            ("assistant", "assistant"),
            ("system", "system"),
            ("error", "error"),
            ("User", "User"),
        ],
    )
    def test_non_tool_roles_preserved(self, raw, expected):
        # Genuine system messages must NOT be remapped to toolResult — that
        # cannot be done from the role alone (see normalize_message_role docstring).
        assert normalize_message_role(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_empty_returns_blank(self, raw):
        assert normalize_message_role(raw) == ""

    def test_idempotent(self):
        once = normalize_message_role("tool_result")
        twice = normalize_message_role(once)
        assert once == twice == "toolResult"

    def test_unknown_role_not_swallowed(self):
        # A genuinely unknown role is returned stripped, not force-merged.
        assert normalize_message_role("custom_role") == "custom_role"


class TestSaveMessageRoleWriteBoundary:
    """save_message must normalize role before writing daily_messages."""

    def _role_param(self, db_mock) -> str:
        """Extract the role value from the last execute() call's params.

        INSERT column order: date, tool_name, host_name, message_id, parent_id,
        role, ... so role is at params index 5.
        """
        params = db_mock.execute.call_args[0][1]
        return params[5]

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_tool_result_alias(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-18", tool_name="qwen", message_id="m1", role="tool_result")
        assert self._role_param(db) == "toolResult"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_tool_result_casing(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-18", tool_name="qwen", message_id="m2", role="TOOLRESULT")
        assert self._role_param(db) == "toolResult"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_preserves_system_role(self, _mock_pg):
        # Genuine system messages must survive normalization untouched so they
        # are not misclassified as tool results.
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-18", tool_name="qwen", message_id="m3", role="system")
        assert self._role_param(db) == "system"

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_preserves_user_and_assistant(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-18", tool_name="qwen", message_id="m4", role="user")
        assert self._role_param(db) == "user"
        repo.save_message(date="2026-06-18", tool_name="qwen", message_id="m5", role="assistant")
        assert self._role_param(db) == "assistant"
