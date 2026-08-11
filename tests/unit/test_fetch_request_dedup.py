#!/usr/bin/env python3
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _load_fetch_script(module_file: str, module_name: str, db_url: str):
    os.environ["DATABASE_URL"] = db_url
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))

    from shared import db as db_mod

    importlib.reload(db_mod)

    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS_DIR / module_file)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _init_dest_schema(db_path: Path) -> None:
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
            metadata TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users (id, username, system_account, email) VALUES (?, ?, ?, ?)",
        (1, "rhuang", "rhuang", "rhuang@localhost"),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def fetch_claude_mod(tmp_path):
    dest_db = tmp_path / "claude.sqlite"
    _init_dest_schema(dest_db)
    return _load_fetch_script("fetch_claude.py", "fetch_claude_under_test", f"sqlite:///{dest_db}")


@pytest.fixture
def fetch_qwen_mod(tmp_path):
    dest_db = tmp_path / "qwen.sqlite"
    _init_dest_schema(dest_db)
    return _load_fetch_script("fetch_qwen.py", "fetch_qwen_under_test", f"sqlite:///{dest_db}")


def test_fetch_claude_session_stats_dedup_duplicate_message_ids(fetch_claude_mod, tmp_path):
    ts_iso = datetime.now(timezone.utc).isoformat()
    messages = [
        {
            "date": "2026-06-22",
            "tool_name": "claude",
            "host_name": "localhost",
            "message_id": "dup-1",
            "role": "assistant",
            "content": "final answer",
            "content_blocks": None,
            "tokens_used": 120,
            "input_tokens": 100,
            "output_tokens": 20,
            "model": "claude-sonnet",
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-claude",
            "agent_session_id": "sess-claude-dedup",
            "project_path": "/repo",
        },
        {
            "date": "2026-06-22",
            "tool_name": "claude",
            "host_name": "localhost",
            "message_id": "dup-1",
            "role": "assistant",
            "content": "",
            "content_blocks": None,
            "tokens_used": 120,
            "input_tokens": 100,
            "output_tokens": 20,
            "model": "claude-sonnet",
            "timestamp": ts_iso,
            "sender_name": "rhuang-host-claude",
            "agent_session_id": "sess-claude-dedup",
            "project_path": "/repo",
        },
    ]

    updated = fetch_claude_mod.update_agent_sessions_stats(messages)
    assert updated == 1

    conn = sqlite3.connect(str(tmp_path / "claude.sqlite"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT message_count, request_count, total_tokens FROM agent_sessions WHERE session_id = ?",
        ("sess-claude-dedup",),
    ).fetchone()
    msg_count = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE session_id = ?", ("sess-claude-dedup",)
    ).fetchone()[0]
    conn.close()

    assert row["message_count"] == 1
    assert row["request_count"] == 1
    assert row["total_tokens"] == 120
    assert msg_count == 1


def test_fetch_qwen_daily_and_session_stats_dedup_duplicate_message_ids(fetch_qwen_mod, tmp_path):
    qwen_root = tmp_path / ".qwen" / "projects" / "encoded-proj" / "chats"
    qwen_root.mkdir(parents=True)
    jsonl_path = qwen_root / "sess-qwen-dedup.jsonl"
    duplicate_entry = {
        "type": "assistant",
        "timestamp": "2026-06-22T12:00:00Z",
        "model": "qwen-max",
        "sessionId": "sess-qwen-dedup",
        "uuid": "dup-qwen-1",
        "message": {"message_id": "dup-qwen-1", "parts": [{"text": "hello"}]},
        "usageMetadata": {
            "promptTokenCount": 80,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 0,
            "cachedContentTokenCount": 0,
            "totalTokenCount": 100,
        },
    }
    with open(jsonl_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(duplicate_entry) + "\n")
        f.write(json.dumps(duplicate_entry) + "\n")

    daily, messages = fetch_qwen_mod.process_jsonl_file(jsonl_path, "localhost", "rhuang")
    assert daily["2026-06-22"]["request_count"] == 1
    assert len(messages) == 2

    updated = fetch_qwen_mod.update_agent_sessions_stats(messages)
    assert updated == 1

    conn = sqlite3.connect(str(tmp_path / "qwen.sqlite"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT message_count, request_count, total_tokens FROM agent_sessions WHERE session_id = ?",
        ("sess-qwen-dedup",),
    ).fetchone()
    msg_count = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE session_id = ?", ("sess-qwen-dedup",)
    ).fetchone()[0]
    conn.close()

    assert row["message_count"] == 1
    assert row["request_count"] == 1
    assert row["total_tokens"] == 100
    assert msg_count == 1
