#!/usr/bin/env python3
"""Unit tests for scripts/fetch_zcode.py.

Validates that fetch_zcode:
  - reuses the ZcodeSession parser from remote-agent/session_sync.py,
  - converts a parsed ZCode session into the fetch message-dict shape,
  - upserts agent_sessions (tool_name='zcode', session_type='session',
    status='completed') and inserts session_messages.

The ZCode source DB is a throwaway SQLite file built with ZCode's real schema
(reused from tests/unit/test_zcode_session_sync.py). The destination DB is a
throwaway SQLite file so no PostgreSQL or shared state is required.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REMOTE_AGENT_DIR = _REPO_ROOT / "remote-agent"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _load_fetch_zcode(tmp_db_url: str):
    """Load scripts/fetch_zcode.py as an isolated module against a temp DB.

    Setting DATABASE_URL before import routes shared.db at the temp SQLite file.
    We (re)load shared.db so the env var takes effect for this process.
    """
    import os

    os.environ["DATABASE_URL"] = tmp_db_url
    os.environ["FETCH_USE_SUDO"] = "false"

    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    if str(_REMOTE_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(_REMOTE_AGENT_DIR))

    # Force shared.db to pick up the new DATABASE_URL.
    import importlib

    from shared import db as db_mod

    importlib.reload(db_mod)

    spec = importlib.util.spec_from_file_location(
        "fetch_zcode_under_test", _SCRIPTS_DIR / "fetch_zcode.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _build_zcode_source_db(db_path: Path) -> None:
    """Create a minimal ZCode-schema DB (session/message/part/turn_usage)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE session (
            id text primary key,
            directory text not null,
            time_created integer not null,
            time_updated integer not null,
            time_archived integer,
            task_type text not null default 'interactive',
            title text not null default ''
        );
        CREATE TABLE message (
            id text primary key,
            session_id text not null,
            time_created integer not null,
            data text not null
        );
        CREATE TABLE part (
            id text primary key,
            message_id text not null,
            session_id text not null,
            data text not null
        );
        CREATE TABLE turn_usage (
            session_id text not null,
            turn_id text not null,
            input_tokens integer not null default 0,
            output_tokens integer not null default 0,
            PRIMARY KEY (session_id, turn_id)
        );
        """
    )
    conn.commit()
    conn.close()


def _insert_zcode_message(
    db_path: Path,
    msg_id: str,
    session_id: str,
    time_created: int,
    data: dict,
    parts: list[dict] | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
        (msg_id, session_id, time_created, json.dumps(data)),
    )
    if parts:
        for i, part in enumerate(parts):
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, data) VALUES (?, ?, ?, ?)",
                (f"{msg_id}_part{i}", msg_id, session_id, json.dumps(part)),
            )
    conn.commit()
    conn.close()


def _insert_turn_usage(
    db_path: Path, session_id: str, turn_id: str, input_t: int, output_t: int
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO turn_usage (session_id, turn_id, input_tokens, output_tokens) "
        "VALUES (?, ?, ?, ?)",
        (session_id, turn_id, input_t, output_t),
    )
    conn.commit()
    conn.close()


def _init_dest_schema(db_path: Path) -> None:
    """Create the destination tables fetch_zcode writes to (minimal subset)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            system_account TEXT,
            email TEXT
        );
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            session_type TEXT DEFAULT 'chat',
            title TEXT,
            tool_name TEXT NOT NULL,
            host_name TEXT DEFAULT 'localhost',
            user_id INTEGER,
            status TEXT DEFAULT 'active',
            project_path TEXT,
            message_count INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            model TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            timestamp TEXT,
            metadata TEXT,
            source TEXT DEFAULT ''
        );
        CREATE TABLE daily_usage (
            date TEXT,
            tool_name TEXT,
            host_name TEXT,
            tokens_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_tokens INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            models_used TEXT,
            PRIMARY KEY (date, tool_name, host_name)
        );
        CREATE TABLE daily_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            tool_name TEXT,
            host_name TEXT,
            message_id TEXT,
            role TEXT,
            content TEXT,
            tokens_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            model TEXT,
            sender_id TEXT,
            sender_name TEXT,
            timestamp TEXT
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def fetch_mod(tmp_path, monkeypatch):
    """Load fetch_zcode against a temp destination DB and seed a user."""
    dest_db = tmp_path / "dest.sqlite"
    _init_dest_schema(dest_db)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dest_db}")
    mod = _load_fetch_zcode(f"sqlite:///{dest_db}")

    # Seed a user whose system_account matches the default sender_name prefix.
    conn = sqlite3.connect(str(dest_db))
    conn.execute(
        "INSERT INTO users (id, username, system_account, email) VALUES (?, ?, ?, ?)",
        (1, "rhuang", "rhuang", "rhuang@localhost"),
    )
    conn.commit()
    conn.close()
    return mod


# --------------------------------------------------------------------------- #
# process_zcode_session — message dict shape
# --------------------------------------------------------------------------- #


def test_process_zcode_session_returns_messages_and_project(fetch_mod, tmp_path):
    src_db = tmp_path / "zcode.sqlite"
    _build_zcode_source_db(src_db)
    sid = "sess_abc123"
    conn = sqlite3.connect(str(src_db))
    conn.execute(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, "/Users/me/repo", 1700000000000, 1700000001000, None, "interactive", "test"),
    )
    conn.commit()
    conn.close()
    _insert_zcode_message(
        src_db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "user"},
        parts=[{"type": "text", "text": "hello world"}],
    )
    _insert_zcode_message(
        src_db,
        "msg_2",
        sid,
        1700000000200,
        {"role": "assistant", "modelID": "GLM-5.2", "tokens": {"input": 100, "output": 10}},
        parts=[{"type": "text", "text": "hi there"}],
    )
    # Authoritative session-level tokens come from turn_usage, NOT the sparse
    # per-message data.tokens above. Seed a turn_usage row with different
    # numbers to prove the session totals win.
    _insert_turn_usage(src_db, sid, "turn_1", input_t=250, output_t=30)

    daily, messages, project = fetch_mod.process_zcode_session(sid, src_db, "localhost", None)

    assert project == "/Users/me/repo"
    assert len(messages) == 2
    assert messages[0]["tool_name"] == "zcode"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello world"
    assert messages[0]["agent_session_id"] == sid
    # Per-message tokens are 0; session totals injected into first assistant msg.
    assert messages[0]["tokens_used"] == 0
    assert messages[1]["role"] == "assistant"
    assert messages[1]["input_tokens"] == 250
    assert messages[1]["output_tokens"] == 30
    assert messages[1]["tokens_used"] == 280
    # daily aggregates use session totals (250 input + 30 output = 280)
    assert len(daily) == 1
    only_date = next(iter(daily))
    assert daily[only_date]["prompt_tokens"] == 250
    assert daily[only_date]["candidates_tokens"] == 30
    assert daily[only_date]["total_tokens"] == 280
    assert daily[only_date]["request_count"] == 1


def test_process_zcode_session_request_count_counts_assistant_messages(fetch_mod, tmp_path):
    """Regression: request_count must count assistant messages, not distinct
    dates. A single-day session with multiple assistant turns should report a
    count > 1 (previously collapsed to 1 by counting distinct dates)."""
    src_db = tmp_path / "zcode.sqlite"
    _build_zcode_source_db(src_db)
    sid = "sess_multi"
    conn = sqlite3.connect(str(src_db))
    conn.execute(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, "/repo", 1700000000000, 1700000001000, None, "interactive", "t"),
    )
    conn.commit()
    conn.close()
    # user, assistant, user, assistant — 2 assistant turns, same day
    _insert_zcode_message(
        src_db, "m1", sid, 1700000000100, {"role": "user"}, parts=[{"type": "text", "text": "q1"}]
    )
    _insert_zcode_message(
        src_db,
        "m2",
        sid,
        1700000000200,
        {"role": "assistant"},
        parts=[{"type": "text", "text": "a1"}],
    )
    _insert_zcode_message(
        src_db, "m3", sid, 1700000000300, {"role": "user"}, parts=[{"type": "text", "text": "q2"}]
    )
    _insert_zcode_message(
        src_db,
        "m4",
        sid,
        1700000000400,
        {"role": "assistant"},
        parts=[{"type": "text", "text": "a2"}],
    )

    daily, messages, _project = fetch_mod.process_zcode_session(sid, src_db, "localhost", None)

    assert len(messages) == 4
    only_date = next(iter(daily))
    assert daily[only_date]["request_count"] == 2


def test_process_zcode_session_skips_empty(fetch_mod, tmp_path):
    src_db = tmp_path / "zcode.sqlite"
    _build_zcode_source_db(src_db)
    sid = "sess_empty"
    conn = sqlite3.connect(str(src_db))
    conn.execute(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, "/repo", 1700000000000, 1700000001000, None, "interactive", ""),
    )
    conn.commit()
    conn.close()
    # message with no text parts -> dropped by ZcodeSession
    _insert_zcode_message(src_db, "msg_1", sid, 1700000000100, {"role": "user"}, parts=[])

    daily, messages, project = fetch_mod.process_zcode_session(sid, src_db, "localhost", None)
    assert messages == []
    assert daily == {}


# --------------------------------------------------------------------------- #
# update_agent_sessions_stats — DB writes
# --------------------------------------------------------------------------- #


def test_update_agent_sessions_stats_inserts_session_and_messages(fetch_mod, tmp_path):
    """A new session gets an agent_sessions row + session_messages rows."""
    ts_iso = datetime.now(timezone.utc).isoformat()
    messages = [
        {
            "date": "2026-06-20",
            "tool_name": "zcode",
            "host_name": "localhost",
            "message_id": "m1",
            "role": "user",
            "content": "hello",
            "content_blocks": None,
            "tokens_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "model": None,
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-zcode",
            "agent_session_id": "sess_new1",
            "project_path": "/repo",
        },
        {
            "date": "2026-06-20",
            "tool_name": "zcode",
            "host_name": "localhost",
            "message_id": "m2",
            "role": "assistant",
            "content": "hi there",
            "content_blocks": None,
            "tokens_used": 110,
            "input_tokens": 100,
            "output_tokens": 10,
            "model": "GLM-5.2",
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-zcode",
            "agent_session_id": "sess_new1",
            "project_path": "/repo",
        },
    ]

    updated = fetch_mod.update_agent_sessions_stats(messages)
    assert updated == 1

    dest_db = tmp_path / "dest.sqlite"
    conn = sqlite3.connect(str(dest_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM agent_sessions WHERE session_id = ?", ("sess_new1",)
    ).fetchone()
    assert row is not None
    assert row["tool_name"] == "zcode"
    assert row["session_type"] == "session"
    assert row["status"] == "completed"
    assert row["message_count"] == 2
    assert row["request_count"] == 1
    assert row["total_tokens"] == 110
    assert row["model"] == "GLM-5.2"
    assert row["project_path"] == "/repo"
    # user_id resolved from sender_name prefix "rhuang"
    assert row["user_id"] == 1

    msg_rows = conn.execute(
        "SELECT role, source FROM session_messages WHERE session_id = ? ORDER BY role",
        ("sess_new1",),
    ).fetchall()
    roles = {r["role"] for r in msg_rows}
    assert roles == {"user", "assistant"}
    assert {r["source"] for r in msg_rows} == {"fetch_zcode"}
    conn.close()


def test_update_agent_sessions_stats_idempotent(fetch_mod, tmp_path):
    """Re-running on the same messages does not duplicate session_messages."""
    ts_iso = datetime.now(timezone.utc).isoformat()
    messages = [
        {
            "date": "2026-06-20",
            "tool_name": "zcode",
            "host_name": "localhost",
            "message_id": "m1",
            "role": "user",
            "content": "q",
            "content_blocks": None,
            "tokens_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "model": None,
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-zcode",
            "agent_session_id": "sess_dup",
            "project_path": "/repo",
        },
    ]
    fetch_mod.update_agent_sessions_stats(messages)
    fetch_mod.update_agent_sessions_stats(messages)

    dest_db = tmp_path / "dest.sqlite"
    conn = sqlite3.connect(str(dest_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE session_id = ?", ("sess_dup",)
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_update_agent_sessions_stats_skips_existing_workflow_session(fetch_mod, tmp_path):
    """Fetcher must not backfill raw transcript rows into workflow-owned sessions."""
    ts_iso = datetime.now(timezone.utc).isoformat()
    dest_db = tmp_path / "dest.sqlite"
    conn = sqlite3.connect(str(dest_db))
    conn.execute(
        """
        INSERT INTO agent_sessions
        (session_id, session_type, title, tool_name, host_name, user_id, status, project_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess_workflow", "workflow", "wf", "zcode", "localhost", 1, "completed", "/repo"),
    )
    conn.commit()
    conn.close()

    messages = [
        {
            "date": "2026-06-20",
            "tool_name": "zcode",
            "host_name": "localhost",
            "message_id": "m1",
            "role": "assistant",
            "content": "raw sync content",
            "content_blocks": None,
            "tokens_used": 10,
            "input_tokens": 6,
            "output_tokens": 4,
            "model": "GLM-5.2",
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-zcode",
            "agent_session_id": "sess_workflow",
            "project_path": "/repo",
        },
    ]

    updated = fetch_mod.update_agent_sessions_stats(messages)
    assert updated == 0

    conn = sqlite3.connect(str(dest_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE session_id = ?", ("sess_workflow",)
    ).fetchone()[0]
    conn.close()
    assert count == 0


# --------------------------------------------------------------------------- #
# _iter_candidate_sessions — filtering
# --------------------------------------------------------------------------- #


def test_iter_candidate_sessions_filters_archived_and_noninteractive(fetch_mod, tmp_path):
    src_db = tmp_path / "zcode.sqlite"
    _build_zcode_source_db(src_db)
    conn = sqlite3.connect(str(src_db))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn.executemany(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("sess_keep", "/a", now_ms, now_ms, None, "interactive", "t"),
            ("sess_archived", "/b", now_ms, now_ms, now_ms, "interactive", "t"),
            ("sess_subagent", "/c", now_ms, now_ms, None, "subagent_child", "t"),
        ],
    )
    conn.commit()
    conn.close()

    candidates = fetch_mod._iter_candidate_sessions(src_db, days=7, recent=False)
    ids = {sid for sid, _ in candidates}
    assert ids == {"sess_keep"}


def test_iter_candidate_sessions_days_filter(fetch_mod, tmp_path):
    src_db = tmp_path / "zcode.sqlite"
    _build_zcode_source_db(src_db)
    old_ms = int((datetime.now(timezone.utc).timestamp() - 30 * 86400) * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn = sqlite3.connect(str(src_db))
    conn.executemany(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("sess_old", "/a", old_ms, old_ms, None, "interactive", "t"),
            ("sess_recent", "/b", now_ms, now_ms, None, "interactive", "t"),
        ],
    )
    conn.commit()
    conn.close()

    candidates = fetch_mod._iter_candidate_sessions(src_db, days=7, recent=False)
    ids = {sid for sid, _ in candidates}
    assert ids == {"sess_recent"}
