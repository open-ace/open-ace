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


class TestSharedDbWriteBoundary:
    """scripts/shared/db.py is the write path the fetch scripts (fetch_openclaw,
    fetch_claude, ...) use via save_message / save_messages_batch. It must
    normalize tool-result spellings to ``tool`` before they reach
    daily_messages, otherwise OpenClaw transcripts keep writing the native
    ``toolResult`` and the conversation-detail role filter relapses into
    "no messages found" (issue #830).
    """

    @pytest.fixture()
    def shared_db(self, monkeypatch, tmp_path):
        """Load scripts/shared/db.py against a file-backed SQLite database.

        Adds scripts/ to sys.path so ``from shared import db`` resolves, and
        points get_connection at a temp file DB with the daily_messages table
        created. A file-backed DB survives save_message's conn.close() between
        the write and the assertion (in-memory :memory: would not).
        """
        import os
        import sqlite3
        import sys

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        scripts_dir = os.path.join(repo_root, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        import importlib

        db = importlib.import_module("shared.db")

        db_path = str(tmp_path / "test.db")

        def _connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        # Create the daily_messages table on the file-backed DB.
        init_conn = _connect()
        init_conn.execute(
            """
            CREATE TABLE daily_messages (
                date TEXT,
                tool_name TEXT,
                host_name TEXT,
                message_id TEXT,
                parent_id TEXT,
                role TEXT,
                content TEXT,
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                message_source TEXT,
                feishu_conversation_id TEXT,
                group_subject TEXT,
                is_group_chat INTEGER,
                agent_session_id TEXT,
                conversation_id TEXT,
                PRIMARY KEY (date, tool_name, host_name, message_id)
            )
            """
        )
        init_conn.commit()
        init_conn.close()

        monkeypatch.setattr(db, "get_connection", _connect)
        monkeypatch.setattr(db, "is_postgresql", lambda: False)
        return db, db_path

    def _fetch_role(self, db_path, message_id):
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT role FROM daily_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        conn.close()
        return row["role"] if row else None

    def test_save_message_normalizes_tool_result_alias(self, shared_db):
        db, db_path = shared_db
        db.save_message(
            date="2026-06-21",
            tool_name="openclaw",
            message_id="m1",
            role="toolResult",
            content="result payload",
        )
        assert self._fetch_role(db_path, "m1") == "tool"

    def test_save_message_normalizes_tool_result_snake_case(self, shared_db):
        db, db_path = shared_db
        db.save_message(
            date="2026-06-21",
            tool_name="openclaw",
            message_id="m2",
            role="tool_result",
            content="result payload",
        )
        assert self._fetch_role(db_path, "m2") == "tool"

    def test_save_message_preserves_canonical_tool_and_other_roles(self, shared_db):
        db, db_path = shared_db
        db.save_message(
            date="2026-06-21", tool_name="openclaw", message_id="m3", role="tool", content="x"
        )
        db.save_message(
            date="2026-06-21", tool_name="openclaw", message_id="m4", role="assistant", content="x"
        )
        assert self._fetch_role(db_path, "m3") == "tool"
        assert self._fetch_role(db_path, "m4") == "assistant"

    def test_save_messages_batch_normalizes_tool_result_alias(self, shared_db):
        db, db_path = shared_db
        messages = [
            {
                "date": "2026-06-21",
                "tool_name": "openclaw",
                "host_name": "localhost",
                "message_id": "b1",
                "role": "toolResult",
                "content": "batch result",
                "tokens_used": 0,
            },
            {
                "date": "2026-06-21",
                "tool_name": "openclaw",
                "host_name": "localhost",
                "message_id": "b2",
                "role": "TOOL_RESULT",
                "content": "batch result 2",
                "tokens_used": 0,
            },
            {
                "date": "2026-06-21",
                "tool_name": "openclaw",
                "host_name": "localhost",
                "message_id": "b3",
                "role": "user",
                "content": "a question",
                "tokens_used": 0,
            },
        ]
        saved = db.save_messages_batch(messages, batch_size=10)
        assert saved == 3
        assert self._fetch_role(db_path, "b1") == "tool"
        assert self._fetch_role(db_path, "b2") == "tool"
        assert self._fetch_role(db_path, "b3") == "user"
