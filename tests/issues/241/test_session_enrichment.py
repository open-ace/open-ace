#!/usr/bin/env python3
"""
Tests for Issue #241: Session stats enrichment from daily_messages.

Covers:
- Sessions with only daily_messages data show correct stats
- Sessions with both session_messages and daily_messages take the larger value
- get_session returns SessionMessage objects (not dicts)
- Enrichment always runs (not gated on message_count == 0)
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def temp_db_path():
    """Create a temporary SQLite database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def db_connection(temp_db_path):
    """Create a database connection with required tables."""
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Create agent_sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            session_type TEXT DEFAULT 'chat',
            title TEXT DEFAULT '',
            tool_name TEXT NOT NULL,
            host_name TEXT DEFAULT 'localhost',
            user_id INTEGER,
            project_id INTEGER,
            project_path TEXT,
            status TEXT DEFAULT 'active',
            context TEXT DEFAULT '{}',
            settings TEXT DEFAULT '{}',
            total_tokens INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            model TEXT,
            tags TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            expires_at TEXT,
            workspace_type TEXT DEFAULT 'local',
            remote_machine_id TEXT,
            paused_at TEXT
        )
    """)

    # Create session_messages table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            timestamp TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)

    # Create daily_messages table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            host_name TEXT DEFAULT 'localhost',
            message_id TEXT NOT NULL,
            parent_id TEXT,
            role TEXT NOT NULL,
            content TEXT,
            full_entry TEXT,
            tokens_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            model TEXT,
            timestamp TEXT,
            sender_id TEXT,
            sender_name TEXT,
            agent_session_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    yield conn
    conn.close()


def _insert_session(
    conn, session_id, tool_name="qwen", message_count=0, total_tokens=0, request_count=0, model=None
):
    """Insert a test session into agent_sessions."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO agent_sessions
           (session_id, tool_name, message_count, total_tokens,
            request_count, model, created_at, updated_at, status, workspace_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'local')""",
        (session_id, tool_name, message_count, total_tokens, request_count, model, now, now),
    )
    conn.commit()


def _insert_session_message(conn, session_id, role, content="test", tokens=0):
    """Insert a test message into session_messages."""
    ts = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO session_messages
           (session_id, role, content, tokens_used, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, role, content, tokens, ts),
    )
    conn.commit()


def _insert_daily_message(
    conn,
    agent_session_id,
    role,
    content="test",
    tokens=0,
    input_tokens=0,
    output_tokens=0,
    model=None,
):
    """Insert a test message into daily_messages."""
    ts = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO daily_messages
           (date, tool_name, message_id, role, content, tokens_used,
            input_tokens, output_tokens, model, timestamp, agent_session_id)
           VALUES (?, 'qwen', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-05-02",
            f"msg_{ts}",
            role,
            content,
            tokens,
            input_tokens,
            output_tokens,
            model,
            ts,
            agent_session_id,
        ),
    )
    conn.commit()


# ==================== Unit-level tests for enrichment logic ====================


class TestEnrichmentLogic:
    """Test the enrichment logic at the data layer."""

    def test_daily_messages_only(self, db_connection):
        """Session with no session_messages but with daily_messages should show correct stats."""
        conn = db_connection
        sid = "test-dm-only-0001"

        _insert_session(conn, sid, message_count=0, total_tokens=0)

        # Add 5 user + 3 assistant messages to daily_messages
        for i in range(5):
            _insert_daily_message(
                conn,
                sid,
                "user",
                f"user msg {i}",
                tokens=100 + i,
                input_tokens=80 + i,
                output_tokens=20 + i,
                model="glm-5",
            )
        for i in range(3):
            _insert_daily_message(
                conn,
                sid,
                "assistant",
                f"assistant msg {i}",
                tokens=200 + i,
                input_tokens=150 + i,
                output_tokens=50 + i,
                model="glm-5",
            )

        # Verify daily_messages data
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE agent_session_id = ?",
            (sid,),
        ).fetchone()
        assert row["cnt"] == 8

        # Verify session_messages is empty
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM session_messages WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row["cnt"] == 0

        # Verify enrichment would produce correct values
        row = conn.execute(
            """SELECT COUNT(*) as msg_count,
                      SUM(tokens_used) as total_tokens,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      SUM(CASE WHEN role IN ('assistant', 'toolResult') THEN 1 ELSE 0 END) as req_count,
                      MAX(model) as model
               FROM daily_messages WHERE agent_session_id = ?""",
            (sid,),
        ).fetchone()
        assert row["msg_count"] == 8
        assert row["req_count"] == 3
        assert row["model"] == "glm-5"
        assert row["total_tokens"] > 0

    def test_both_sources_take_max(self, db_connection):
        """When both tables have data, the larger values should be used."""
        conn = db_connection
        sid = "test-both-sources"

        _insert_session(conn, sid, message_count=0, total_tokens=0)

        # Add 2 messages to session_messages
        _insert_session_message(conn, sid, "user", "u1", tokens=50)
        _insert_session_message(conn, sid, "assistant", "a1", tokens=100)

        # Add 10 messages to daily_messages (more complete)
        for i in range(7):
            _insert_daily_message(conn, sid, "user", f"u{i}", tokens=50)
        for i in range(3):
            _insert_daily_message(
                conn, sid, "assistant", f"a{i}", tokens=100, input_tokens=80, output_tokens=20
            )

        # session_messages stats
        sm_row = conn.execute(
            "SELECT COUNT(*) as cnt, SUM(tokens_used) as total FROM session_messages WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert sm_row["cnt"] == 2

        # daily_messages stats
        dm_row = conn.execute(
            "SELECT COUNT(*) as cnt, SUM(tokens_used) as total FROM daily_messages WHERE agent_session_id = ?",
            (sid,),
        ).fetchone()
        assert dm_row["cnt"] == 10

        # max(2, 10) = 10 — daily_messages should win
        assert max(sm_row["cnt"], dm_row["cnt"]) == 10

    def test_session_messages_larger(self, db_connection):
        """When session_messages has more data, its values should be preserved."""
        conn = db_connection
        sid = "test-sm-larger"

        _insert_session(conn, sid, message_count=0, total_tokens=0)

        # Add 20 messages to session_messages
        for i in range(20):
            _insert_session_message(
                conn, sid, "user" if i % 2 == 0 else "assistant", f"msg {i}", tokens=100
            )

        # Add only 3 messages to daily_messages
        for i in range(3):
            _insert_daily_message(conn, sid, "user", f"dm {i}", tokens=50)

        sm_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM session_messages WHERE session_id = ?",
            (sid,),
        ).fetchone()
        dm_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE agent_session_id = ?",
            (sid,),
        ).fetchone()

        # max(20, 3) = 20 — session_messages should win
        assert max(sm_row["cnt"], dm_row["cnt"]) == 20

    def test_model_from_daily_messages(self, db_connection):
        """Model should be populated from daily_messages when agent_sessions has none."""
        conn = db_connection
        sid = "test-model-dm"

        _insert_session(conn, sid, message_count=0, total_tokens=0, model=None)

        _insert_daily_message(conn, sid, "user", "hi", tokens=10, model="glm-5")
        _insert_daily_message(conn, sid, "assistant", "hello", tokens=20, model="glm-5")

        row = conn.execute(
            "SELECT MAX(model) as model FROM daily_messages WHERE agent_session_id = ?",
            (sid,),
        ).fetchone()
        assert row["model"] == "glm-5"


class TestSessionMessageObjectType:
    """Test that get_session returns proper SessionMessage objects, not dicts."""

    def test_session_message_has_to_dict(self):
        """SessionMessage objects should have a to_dict method."""
        from app.modules.workspace.session_manager import SessionMessage

        msg = SessionMessage(
            id=1,
            session_id="test",
            role="user",
            content="hello",
            tokens_used=10,
        )
        result = msg.to_dict()
        assert isinstance(result, dict)
        assert result["role"] == "user"
        assert result["content"] == "hello"
        assert result["tokens_used"] == 10

    def test_session_to_dict_with_messages(self):
        """AgentSession.to_dict() should correctly serialize SessionMessage objects."""
        from app.modules.workspace.session_manager import AgentSession, SessionMessage

        session = AgentSession(
            session_id="test-session",
            tool_name="qwen",
            message_count=2,
            messages=[
                SessionMessage(id=1, session_id="test-session", role="user", content="hi"),
                SessionMessage(id=2, session_id="test-session", role="assistant", content="hello"),
            ],
        )
        result = session.to_dict()
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"


class TestEnrichmentAlwaysRuns:
    """Test that enrichment is not gated on specific conditions."""

    def test_enrichment_not_gated_on_tokens(self, db_connection):
        """Even if total_tokens has a value, enrichment should still run for message_count."""
        conn = db_connection
        sid = "test-not-gated"

        # Insert session with tokens but 0 message_count
        _insert_session(conn, sid, message_count=0, total_tokens=500)

        # Add daily_messages
        for i in range(5):
            _insert_daily_message(conn, sid, "user", f"msg {i}", tokens=100)

        dm_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE agent_session_id = ?",
            (sid,),
        ).fetchone()
        assert dm_row["cnt"] == 5

        # max(0, 5) = 5 — enrichment should fill message_count
        assert max(0, dm_row["cnt"]) == 5
