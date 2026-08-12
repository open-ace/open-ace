from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.llm_proxy_handler import _record_llm_usage
from app.modules.workspace.session_manager import AgentSession, SessionManager


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "session_messages_contract.db"))
    sm._ensure_tables()
    conn = sm._get_connection()
    cur = conn.cursor()
    for col in ("project_id", "project_path"):
        try:
            cur.execute(f"ALTER TABLE agent_sessions ADD COLUMN {col} TEXT")
        except Exception:
            pass
    conn.commit()
    conn.close()
    return sm


def _create_session(sm: SessionManager, session_id: str = "sess-1128") -> AgentSession:
    return sm.create_session(tool_name="claude", session_id=session_id, user_id=1)


def test_add_message_merges_duplicate_message_id_without_double_count(sqlite_sm):
    _create_session(sqlite_sm)

    sqlite_sm.add_message(
        session_id="sess-1128",
        role="assistant",
        content="final assistant response",
        tokens_used=120,
        count_usage=True,
        metadata={"message_id": "msg-1", "content_blocks": [{"type": "text"}]},
    )
    sqlite_sm.add_message(
        session_id="sess-1128",
        role="assistant",
        content="short",
        tokens_used=0,
        milestone_id="ms-1",
        count_usage=True,
        metadata={"message_id": "msg-1"},
    )

    messages = sqlite_sm.get_messages("sess-1128")
    session = sqlite_sm.get_session("sess-1128")

    assert len(messages) == 1
    assert messages[0].content == "final assistant response"
    assert messages[0].milestone_id == "ms-1"
    assert messages[0].metadata["content_blocks"] == [{"type": "text"}]
    assert session.message_count == 1
    assert session.request_count == 1
    assert session.total_tokens == 120


def test_append_transcript_message_is_summary_side_effect_free(sqlite_sm):
    _create_session(sqlite_sm, session_id="sess-1128-transcript-only")

    sqlite_sm.append_transcript_message(
        session_id="sess-1128-transcript-only",
        role="assistant",
        content="transcript only",
        tokens_used=33,
        source="remote_sync",
        external_message_id="sync-1",
    )

    session = sqlite_sm.get_session("sess-1128-transcript-only")
    assert session.message_count == 0
    assert session.request_count == 0
    assert session.total_tokens == 0


def test_add_message_persists_structured_session_message_columns(sqlite_sm):
    _create_session(sqlite_sm, session_id="sess-1128-structured")

    sqlite_sm.append_transcript_message(
        session_id="sess-1128-structured",
        role="assistant",
        content="structured response",
        tokens_used=42,
        timestamp="2026-06-22T12:34:56Z",
        source="autonomous_local_runner",
        external_message_id="ext-42",
        metadata={"content_blocks": [{"type": "text", "text": "structured response"}]},
    )

    conn = sqlite_sm._get_connection()
    row = conn.execute(
        """
        SELECT source_timestamp, source, external_message_id, content_blocks, metadata
        FROM session_messages
        WHERE session_id = ?
        """,
        ("sess-1128-structured",),
    ).fetchone()
    conn.close()

    assert row["source_timestamp"] == "2026-06-22T12:34:56"
    assert row["source"] == "autonomous_local_runner"
    assert row["external_message_id"] == "ext-42"
    assert json.loads(row["content_blocks"]) == [{"type": "text", "text": "structured response"}]

    message = sqlite_sm.get_messages("sess-1128-structured")[0]
    assert message.source_timestamp.isoformat() == "2026-06-22T12:34:56"
    assert message.source == "autonomous_local_runner"
    assert message.external_message_id == "ext-42"
    assert message.content_blocks == [{"type": "text", "text": "structured response"}]
    assert message.metadata["content_blocks"] == [{"type": "text", "text": "structured response"}]
    assert json.loads(row["metadata"])["source"] == "autonomous_local_runner"


def test_ensure_tables_migrates_legacy_session_messages_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    db_path = tmp_path / "legacy_session_messages.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            session_type TEXT DEFAULT 'chat',
            title TEXT,
            tool_name TEXT NOT NULL,
            host_name TEXT DEFAULT 'localhost',
            user_id INTEGER,
            status TEXT DEFAULT 'active',
            context TEXT,
            settings TEXT,
            total_tokens INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            model TEXT,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            expires_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    sm = SessionManager(db_path=str(db_path))
    sm._ensure_tables()

    conn = sm._get_connection()
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(session_messages)").fetchall()
    }
    conn.close()

    assert {
        "source_timestamp",
        "milestone_id",
        "source",
        "external_message_id",
        "content_blocks",
    }.issubset(columns)


def test_llm_proxy_records_transcript_without_double_count(monkeypatch):
    session = AgentSession(session_id="proxy-1", tool_name="qwen-code", user_id=3)

    class FakeSessionManager:
        def __init__(self):
            self.session = session
            self.calls = []

        def get_session(self, session_id, include_messages=False):
            return self.session

        def update_session(self, updated_session):
            self.session = updated_session
            return True

        def increment_session_usage(
            self,
            session_id,
            message_delta=0,
            request_delta=0,
            total_tokens_delta=0,
            total_input_delta=0,
            total_output_delta=0,
        ):
            self.session.message_count = (self.session.message_count or 0) + message_delta
            self.session.request_count = (self.session.request_count or 0) + request_delta
            self.session.total_tokens = (self.session.total_tokens or 0) + total_tokens_delta
            self.session.total_input_tokens = (
                self.session.total_input_tokens or 0
            ) + total_input_delta
            self.session.total_output_tokens = (
                self.session.total_output_tokens or 0
            ) + total_output_delta
            return True

        def append_transcript_message(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(_was_inserted=True)

    fake_sm = FakeSessionManager()

    monkeypatch.setattr(
        "app.modules.workspace.session_manager.get_session_manager", lambda: fake_sm
    )
    monkeypatch.setattr(
        "app.modules.governance.quota_manager.QuotaManager",
        lambda: SimpleNamespace(record_usage=lambda **kwargs: None),
    )
    monkeypatch.setattr(
        "app.repositories.daily_stats_repo.DailyStatsRepository",
        lambda: SimpleNamespace(refresh_stats=lambda: None),
    )

    request_body = b'{"messages":[{"role":"user","content":"hello"}]}'
    response_body = (
        b'{"model":"gpt-4.1","usage":{"prompt_tokens":11,"completion_tokens":7},'
        b'"choices":[{"message":{"role":"assistant","content":"world"}}]}'
    )

    _record_llm_usage(
        content=response_body,
        session_id="proxy-1",
        user_id=3,
        provider="openai",
        content_type="application/json",
        request_body=request_body,
    )

    assert fake_sm.session.request_count == 1
    assert fake_sm.session.message_count == 2
    assert fake_sm.session.total_tokens == 18
    assert fake_sm.session.total_input_tokens == 11
    assert fake_sm.session.total_output_tokens == 7
    assert [call["role"] for call in fake_sm.calls] == ["user", "assistant"]
    assert all(call.get("source") == "llm_proxy" for call in fake_sm.calls)
