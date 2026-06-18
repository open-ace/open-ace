"""Unit tests for tool-name normalization.

Guards the ROI cost-breakdown duplicate-tool bug: a single tool must always
normalize to one canonical key regardless of casing, surrounding whitespace,
or alias spelling. Also verifies the write boundaries (save_usage /
save_message) normalize before persisting, so variants can never re-enter
the tables and split downstream aggregates.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.message_repo import MessageRepository
from app.repositories.usage_repo import UsageRepository
from app.utils.tool_names import normalize_tool_name


class TestNormalizeToolName:
    """Pure unit tests for normalize_tool_name."""

    @pytest.mark.parametrize("name", ["qwen", "claude", "codex", "openclaw"])
    def test_canonical_passthrough(self, name):
        assert normalize_tool_name(name) == name

    @pytest.mark.parametrize(
        "variant",
        [
            "qwen-code",
            "qwen-code-cli",
            "Qwen-Code",
            "QWEN-CODE-CLI",
            " qwen-code ",
            "Qwen-Code-Cli",
            "\tqwen-code\n",
        ],
    )
    def test_qwen_aliases_and_case_collapse_to_qwen(self, variant):
        assert normalize_tool_name(variant) == "qwen"

    @pytest.mark.parametrize("variant", ["claude-code", "Claude-Code", " CLAUDE-CODE "])
    def test_claude_aliases_collapse(self, variant):
        assert normalize_tool_name(variant) == "claude"

    @pytest.mark.parametrize(
        "raw,expected",
        [("Codex-CLI", "codex"), ("zcode-code", "zcode"), ("ZCode-CLI", "zcode")],
    )
    def test_other_aliases(self, raw, expected):
        assert normalize_tool_name(raw) == expected

    @pytest.mark.parametrize("raw", ["Qwen", "QWEN", " qwen ", "\tqwen\n", "qWeN"])
    def test_case_and_whitespace_drift(self, raw):
        # The actual prod failure mode: pure case drift that is NOT a known alias.
        assert normalize_tool_name(raw) == "qwen"

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_empty_returns_unknown(self, raw):
        assert normalize_tool_name(raw) == "unknown"

    def test_unknown_tool_lowercased_not_swallowed(self):
        # NEGATIVE: no prefix/fuzzy matching. A future tool whose name merely
        # starts with "qwen" must NOT be force-merged into "qwen".
        assert normalize_tool_name("qwen-coder") == "qwen-coder"
        assert normalize_tool_name("QwenX") == "qwenx"
        # Genuine unknown tools are preserved (lower-cased), never dropped.
        assert normalize_tool_name("SomeNewTool") == "somenewtool"

    def test_idempotent(self):
        once = normalize_tool_name("Qwen-Code")
        twice = normalize_tool_name(once)
        assert once == twice == "qwen"


class TestSaveUsageWriteBoundary:
    """save_usage must normalize tool_name before writing daily_usage."""

    @patch("app.repositories.usage_repo.is_postgresql", return_value=False)
    def test_save_usage_normalizes_alias(self, _mock_pg):
        db, cursor, conn = _wire_connection()
        repo = UsageRepository(db=db)
        repo.save_usage(date="2026-06-18", tool_name="qwen-code", tokens_used=100)
        params = cursor.execute.call_args[0][1]
        assert params[1] == "qwen"  # tool_name is the 2nd positional column

    @patch("app.repositories.usage_repo.is_postgresql", return_value=False)
    def test_save_usage_normalizes_case_drift(self, _mock_pg):
        db, cursor, conn = _wire_connection()
        repo = UsageRepository(db=db)
        repo.save_usage(date="2026-06-18", tool_name=" QWEN ", tokens_used=100)
        params = cursor.execute.call_args[0][1]
        assert params[1] == "qwen"


class TestSaveMessageWriteBoundary:
    """save_message must normalize tool_name before writing daily_messages."""

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_alias(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(
            date="2026-06-18", tool_name="claude-code", message_id="m1", role="assistant"
        )
        params = db.execute.call_args[0][1]
        assert params[1] == "claude"  # tool_name is the 2nd positional column

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_save_message_normalizes_case_drift(self, _mock_pg):
        db = MagicMock()
        repo = MessageRepository(db=db)
        repo.save_message(date="2026-06-18", tool_name=" Qwen ", message_id="m2", role="assistant")
        params = db.execute.call_args[0][1]
        assert params[1] == "qwen"


def _wire_connection():
    """Build a mock Database whose connection() yields a mock conn+cursor."""
    db = MagicMock()
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    db.connection.return_value.__enter__.return_value = conn
    return db, cursor, conn
