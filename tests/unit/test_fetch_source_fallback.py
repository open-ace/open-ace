"""Source-fallback coverage for ``fetch_claude`` / ``fetch_qwen``.

PR #1225 added a ``has_source``-aware fallback INSERT to all four fetch scripts
so ``session_messages.source`` is still written when the structured transcript
columns (``external_message_id`` etc.) are absent — mirroring the existing
``fetch_codex`` / ``fetch_zcode`` behavior.

``test_fetch_zcode`` already asserts the source value for the zcode fetcher;
this module closes the gap for claude and qwen. The destination table is built
WITH a ``source`` column but WITHOUT ``external_message_id``, which forces the
``elif has_source:`` branch (the fallback under test).
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = project_root / "scripts"


def _load_fetch_module(name: str, tmp_db_url: str):
    """Load scripts/<name>.py as an isolated module against a temp DB."""
    os.environ["DATABASE_URL"] = tmp_db_url
    os.environ["FETCH_USE_SUDO"] = "false"
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))

    from shared import db as db_mod

    importlib.reload(db_mod)

    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _init_dest_schema(db_path: Path) -> None:
    """Destination tables — session_messages has ``source`` but NOT
    ``external_message_id``, forcing the has_source fallback branch."""
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
            total_tokens DEFAULT 0,
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
    conn.execute(
        "INSERT INTO users (id, username, system_account, email) VALUES (1, 'rhuang', 'rhuang', 'r@x')"
    )
    conn.commit()
    conn.close()


def _messages(tool_name: str) -> list:
    ts_iso = datetime.now(timezone.utc).isoformat()
    return [
        {
            "date": "2026-06-24",
            "tool_name": tool_name,
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
            "sender_name": "rhuang-host-" + tool_name,
            "agent_session_id": "sess_src1",
            "project_path": "/repo",
        },
        {
            "date": "2026-06-24",
            "tool_name": tool_name,
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
            "sender_name": "rhuang-host-" + tool_name,
            "agent_session_id": "sess_src1",
            "project_path": "/repo",
        },
    ]


@pytest.mark.parametrize(
    "module_name,tool_name,expected_source",
    [
        ("fetch_claude", "claude", "fetch_claude"),
        ("fetch_qwen", "qwen", "fetch_qwen"),
    ],
)
def test_fetch_source_fallback_writes_source(tmp_path, module_name, tool_name, expected_source):
    """When the dest table lacks external_message_id (has_source fallback),
    the fetcher still writes ``source`` to session_messages."""
    dest_db = tmp_path / "dest.sqlite"
    _init_dest_schema(dest_db)
    fetch_mod = _load_fetch_module(module_name, f"sqlite:///{dest_db}")

    updated = fetch_mod.update_agent_sessions_stats(_messages(tool_name))
    assert updated == 1

    conn = sqlite3.connect(str(dest_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT role, source FROM session_messages WHERE session_id = ? ORDER BY role",
        ("sess_src1",),
    ).fetchall()
    conn.close()

    assert {r["role"] for r in rows} == {"user", "assistant"}
    assert {r["source"] for r in rows} == {expected_source}
