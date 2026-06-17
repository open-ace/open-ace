#!/usr/bin/env python3
"""Unit tests for ZCode session sync (ZcodeSession + DB scanner).

Builds a temporary SQLite DB mimicking ZCode's schema and verifies that
ZcodeSession parses sessions/messages/tokens correctly and that the scanner
syncs only interactive, non-archived sessions.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_AGENT_DIR = str(Path(__file__).resolve().parents[2] / "remote-agent")


def _load_session_sync():
    if _AGENT_DIR not in sys.path:
        sys.path.insert(0, _AGENT_DIR)
    spec = importlib.util.spec_from_file_location(
        "session_sync", Path(_AGENT_DIR) / "session_sync.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sync_mod():
    return _load_session_sync()


def _build_db(db_path: Path) -> None:
    """Create a minimal ZCode-schema DB with test data."""
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


def _insert_session(
    db_path: Path,
    session_id: str,
    directory: str = "/tmp/project",
    task_type: str = "interactive",
    time_archived: int | None = None,
    created: int = 1700000000000,
    updated: int = 1700000001000,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO session (id, directory, time_created, time_updated, time_archived, task_type, title) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, directory, created, updated, time_archived, task_type, "test"),
    )
    conn.commit()
    conn.close()


def _insert_message(
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


# --------------------------------------------------------------------------- #
# ZcodeSession.parse
# --------------------------------------------------------------------------- #


def test_parse_extracts_messages_tokens_and_project(sync_mod, tmp_path):
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_abc123"
    _insert_session(db, sid, directory="/Users/me/repo")
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "user"},
        parts=[{"type": "text", "text": "hello world"}],
    )
    _insert_message(
        db,
        "msg_2",
        sid,
        1700000000200,
        {
            "role": "assistant",
            "modelID": "GLM-5.2",
            "tokens": {"total": 110, "input": 100, "output": 10},
        },
        parts=[{"type": "text", "text": "hi there"}, {"type": "reasoning", "text": "(hidden)"}],
    )
    _insert_turn_usage(db, sid, "turn_1", 100, 10)

    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True

    assert session.project_path == "/Users/me/repo"
    assert session.model == "GLM-5.2"
    assert session.message_count == 2
    assert session.total_input_tokens == 100
    assert session.total_output_tokens == 10
    # user message
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "hello world"
    assert session.messages[0]["uuid"] == "msg_1"
    # assistant message — only text parts, reasoning excluded
    assert session.messages[1]["role"] == "assistant"
    assert session.messages[1]["content"] == "hi there"
    assert session.messages[1]["usage"] == {"input_tokens": 100, "output_tokens": 10}


def test_parse_returns_false_for_unknown_session(sync_mod, tmp_path):
    db = tmp_path / "db.sqlite"
    _build_db(db)
    session = sync_mod.ZcodeSession("sess_nonexistent", db)
    assert session.parse() is False


def test_parse_skips_empty_content_messages(sync_mod, tmp_path):
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_empty"
    _insert_session(db, sid)
    _insert_message(db, "msg_1", sid, 1700000000100, {"role": "user"}, parts=[])  # no text
    _insert_message(
        db,
        "msg_2",
        sid,
        1700000000200,
        {"role": "assistant"},
        parts=[{"type": "text", "text": "real content"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True
    assert session.message_count == 1  # only the non-empty assistant message
    assert session.messages[0]["content"] == "real content"


def test_to_sync_payload_shape(sync_mod, tmp_path):
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_payload"
    _insert_session(db, sid, directory="/repo")
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "user"},
        parts=[{"type": "text", "text": "q"}],
    )
    sess = sync_mod.ZcodeSession(sid, db)
    sess.parse()
    out = sess.to_sync_payload("machine-1", "term-1")
    assert out["tool_name"] == "zcode"
    assert out["session_id"] == sid
    assert out["machine_id"] == "machine-1"
    assert out["terminal_id"] == "term-1"
    assert out["project_path"] == "/repo"
    assert isinstance(out["messages"], list)
    assert all("uuid" in m for m in out["messages"])


# --------------------------------------------------------------------------- #
# _extract_parts_text (edge cases)
# --------------------------------------------------------------------------- #


def test_extract_parts_text_handles_nested_braces(sync_mod):
    # A part whose text contains a brace should not break the brace-depth walker.
    blob = '{"type":"text","text":"code { x }"}{"type":"text","text":" more"}'
    result = sync_mod.ZcodeSession._extract_parts_text(blob)
    assert result == "code { x } more"


def test_extract_parts_text_ignores_non_text_parts(sync_mod):
    blob = '{"type":"tool","text":"ignored"}{"type":"text","text":"kept"}'
    assert sync_mod.ZcodeSession._extract_parts_text(blob) == "kept"
