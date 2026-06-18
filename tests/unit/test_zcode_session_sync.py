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
    # A part whose text contains balanced braces must still parse correctly.
    blob = '{"type":"text","text":"code { x }"}{"type":"text","text":" more"}'
    result = sync_mod.ZcodeSession._extract_parts_text(blob)
    assert result == "code { x } more"


def test_extract_parts_text_ignores_non_text_parts(sync_mod):
    blob = '{"type":"tool","text":"ignored"}{"type":"text","text":"kept"}'
    assert sync_mod.ZcodeSession._extract_parts_text(blob) == "kept"


def test_extract_parts_text_handles_unbalanced_braces_in_string(sync_mod):
    # Regression: a brace-depth walker loses ALL text when a string value holds
    # an unbalanced brace (common in code / regex / partial JSON output).
    # raw_decode is string-aware, so the inner brace is not treated as structural.
    blob = '{"type":"text","text":"open: {"}{"type":"text","text":"end"}'
    assert sync_mod.ZcodeSession._extract_parts_text(blob) == "open: {end"


def test_extract_parts_text_handles_unbalanced_close_brace_in_string(sync_mod):
    blob = '{"type":"text","text":"close } here"}{"type":"text","text":" tail"}'
    assert sync_mod.ZcodeSession._extract_parts_text(blob) == "close } here tail"


def test_extract_parts_text_resyncs_after_malformed_part(sync_mod):
    # One malformed object must not discard the subsequent valid parts.
    blob = (
        '{"type":"text","text":"first"}'
        ",{bad malformed object}"  # noqa: E501
        ',{"type":"text","text":"last"}'
    )
    assert sync_mod.ZcodeSession._extract_parts_text(blob) == "firstlast"


# --------------------------------------------------------------------------- #
# Edge cases: model guard + unbalanced braces (post-merge review #1089)
# --------------------------------------------------------------------------- #


def test_parse_model_falls_back_to_nested_dict(sync_mod, tmp_path):
    """When modelID is absent, model is read from data["model"]["modelId"]."""
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_model_nested"
    _insert_session(db, sid)
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "assistant", "model": {"modelId": "GLM-5.2-Nested"}},
        parts=[{"type": "text", "text": "hi"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True
    assert session.model == "GLM-5.2-Nested"
    assert session.messages[0]["model"] == "GLM-5.2-Nested"


def test_parse_handles_string_model_value(sync_mod, tmp_path):
    """data["model"] may be a string, not a dict — must not raise AttributeError."""
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_strmodel"
    _insert_session(db, sid)
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "assistant", "model": "GLM-5.2"},  # string, not dict
        parts=[{"type": "text", "text": "ok"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    # Must not raise; parse succeeds; model falls back to None (no modelID).
    assert session.parse() is True
    assert session.messages[0]["content"] == "ok"
    assert session.model is None  # "GLM-5.2" string not under model.modelId


def test_parse_model_handles_non_dict_model_field(sync_mod, tmp_path):
    """Regression: a non-dict ``model`` value must not raise AttributeError."""
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_model_str"
    _insert_session(db, sid)
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        # model is a plain string, not {"modelId": ...}; must not crash.
        {"role": "assistant", "model": "not-a-dict"},
        parts=[{"type": "text", "text": "hi"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True  # no AttributeError
    assert session.messages[0]["model"] is None
    assert session.model is None


def test_parse_handles_dict_model_with_modelId(sync_mod, tmp_path):
    """When model is a dict with modelId, it is extracted correctly."""
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_dictmodel"
    _insert_session(db, sid)
    _insert_message(
        db,
        "msg_1",
        sid,
        1700000000100,
        {"role": "assistant", "model": {"modelId": "GLM-5.2", "providerId": "zai"}},
        parts=[{"type": "text", "text": "ok"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True
    assert session.model == "GLM-5.2"


def test_extract_parts_text_unbalanced_braces_in_content(sync_mod):
    """Unbalanced braces inside a text value (common in code/JSON/regex) must
    not break extraction — the JSONDecoder approach handles this correctly."""
    blob = '{"type":"text","text":"broken json: {"}' '{"type":"text","text":"} more code"}'
    # raw_decode handles each object; braces inside quoted strings are safe.
    result = sync_mod.ZcodeSession._extract_parts_text(blob)
    assert "broken json:" in result
    assert "more code" in result


def test_scan_loop_survives_malformed_message_data(sync_mod, tmp_path):
    """A message with non-dict model value must not abort the scan loop.

    This simulates the review scenario where one bad row could poison every
    sync cycle. We verify parse() returns normally (no exception propagation).
    """
    db = tmp_path / "db.sqlite"
    _build_db(db)
    sid = "sess_poison"
    _insert_session(db, sid)
    # message with model as a list (another non-dict type)
    _insert_message(
        db,
        "msg_bad",
        sid,
        1700000000100,
        {"role": "assistant", "model": ["unexpected"]},
        parts=[{"type": "text", "text": "survives"}],
    )
    _insert_message(
        db,
        "msg_ok",
        sid,
        1700000000200,
        {"role": "assistant", "modelID": "GLM-5.2"},
        parts=[{"type": "text", "text": "second"}],
    )
    session = sync_mod.ZcodeSession(sid, db)
    assert session.parse() is True
    assert session.message_count == 2  # both messages parsed, no exception
    assert session.model == "GLM-5.2"
