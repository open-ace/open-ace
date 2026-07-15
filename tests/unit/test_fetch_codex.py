"""Unit coverage for Codex fetch token accounting."""

import importlib
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_codex import _process_sessions_dir, process_jsonl_file  # noqa: E402


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


@contextmanager
def _temporary_timezone(tz_name: str):
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = tz_name
        if hasattr(time, "tzset"):
            time.tzset()
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()


def test_process_jsonl_file_keeps_provider_total_and_attributes_turn_to_user(tmp_path):
    jsonl = _write_jsonl(
        tmp_path / "rollout-test-codex.jsonl",
        [
            {
                "timestamp": "2026-07-15T01:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            },
            {
                "timestamp": "2026-07-15T01:00:01Z",
                "type": "turn_context",
                "payload": {"model": "codex-mini"},
            },
            {
                "timestamp": "2026-07-15T01:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                },
            },
            {
                "timestamp": "2026-07-15T01:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "world"}],
                },
            },
            {
                "timestamp": "2026-07-15T01:00:04Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1050,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-07-15T01:00:05Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-1",
                    "last_agent_message": "done",
                },
            },
        ],
    )

    daily, messages, _session_meta, session_tokens = process_jsonl_file(
        jsonl, "localhost", "rhuang"
    )

    assert daily["2026-07-15"]["prompt_tokens"] == 200
    assert daily["2026-07-15"]["candidates_tokens"] == 50
    assert daily["2026-07-15"]["thoughts_tokens"] == 20
    assert daily["2026-07-15"]["cached_tokens"] == 800
    assert daily["2026-07-15"]["total_tokens"] == 1050
    assert daily["2026-07-15"]["request_count"] == 1

    user_msg = next(msg for msg in messages if msg["role"] == "user")
    assistant_msg = next(
        msg
        for msg in messages
        if msg["role"] == "assistant" and msg["message_id"] != user_msg["message_id"]
    )
    assert user_msg["tokens_used"] == 1050
    assert user_msg["input_tokens"] == 200
    assert user_msg["output_tokens"] == 50
    assert user_msg["counts_as_request"] is True
    assert assistant_msg["tokens_used"] == 0

    assert session_tokens["total_tokens"] == 1050
    assert session_tokens["input_tokens"] == 200
    assert session_tokens["output_tokens"] == 50


def test_process_jsonl_file_splits_turns_by_local_date_and_counts_requests_once(tmp_path):
    with _temporary_timezone("Asia/Shanghai"):
        jsonl = _write_jsonl(
            tmp_path / "rollout-test-codex-multi.jsonl",
            [
                {
                    "timestamp": "2026-07-14T15:55:00Z",
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-1"},
                },
                {
                    "timestamp": "2026-07-14T15:55:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "first"}],
                    },
                },
                {
                    "timestamp": "2026-07-14T15:55:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "a1"}],
                    },
                },
                {
                    "timestamp": "2026-07-14T15:55:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "a2"}],
                    },
                },
                {
                    "timestamp": "2026-07-14T15:55:04Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 120,
                                "cached_input_tokens": 80,
                                "output_tokens": 20,
                                "reasoning_output_tokens": 5,
                                "total_tokens": 140,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-07-14T15:55:05Z",
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-1"},
                },
                {
                    "timestamp": "2026-07-14T16:05:00Z",
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-2"},
                },
                {
                    "timestamp": "2026-07-14T16:05:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "second"}],
                    },
                },
                {
                    "timestamp": "2026-07-14T16:05:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "b1"}],
                    },
                },
                {
                    "timestamp": "2026-07-14T16:05:03Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 230,
                                "cached_input_tokens": 10,
                                "output_tokens": 20,
                                "reasoning_output_tokens": 3,
                                "total_tokens": 250,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-07-14T16:05:04Z",
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-2"},
                },
            ],
        )

        daily, messages, _session_meta, session_tokens = process_jsonl_file(
            jsonl, "localhost", "rhuang"
        )

        assert daily["2026-07-14"]["prompt_tokens"] == 40
        assert daily["2026-07-14"]["candidates_tokens"] == 20
        assert daily["2026-07-14"]["cached_tokens"] == 80
        assert daily["2026-07-14"]["total_tokens"] == 140
        assert daily["2026-07-14"]["request_count"] == 1

        assert daily["2026-07-15"]["prompt_tokens"] == 220
        assert daily["2026-07-15"]["candidates_tokens"] == 20
        assert daily["2026-07-15"]["cached_tokens"] == 10
        assert daily["2026-07-15"]["total_tokens"] == 250
        assert daily["2026-07-15"]["request_count"] == 1

        user_messages = [msg for msg in messages if msg["role"] == "user"]
        assert [msg["tokens_used"] for msg in user_messages] == [140, 250]
        assert session_tokens["total_tokens"] == 390
        assert session_tokens["input_tokens"] == 260
        assert session_tokens["output_tokens"] == 40


def test_reimport_replaces_stale_codex_session_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "codex.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from shared import db as shared_db  # noqa: E402

    importlib.reload(shared_db)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
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
    conn.execute(
        """
        INSERT INTO daily_messages (
            date, tool_name, host_name, message_id, role, tokens_used,
            input_tokens, output_tokens, model, timestamp, agent_session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-07-14",
            "codex",
            "localhost",
            "stale-assistant",
            "assistant",
            1050,
            200,
            50,
            "gpt-5.5",
            "2026-07-14T01:00:03Z",
            "sess-1",
        ),
    )
    conn.commit()
    conn.close()

    deleted = shared_db.delete_messages_for_agent_sessions("codex", "localhost", ["sess-1"])
    assert deleted == 1

    saved = shared_db.save_messages_batch(
        [
            {
                "date": "2026-07-14",
                "tool_name": "codex",
                "host_name": "localhost",
                "message_id": "user-1",
                "role": "user",
                "content": "hello",
                "tokens_used": 1050,
                "input_tokens": 200,
                "output_tokens": 50,
                "model": "gpt-5.5",
                "timestamp": "2026-07-14T01:00:02Z",
                "agent_session_id": "sess-1",
                "overwrite_zero_tokens": True,
            },
            {
                "date": "2026-07-14",
                "tool_name": "codex",
                "host_name": "localhost",
                "message_id": "assistant-1",
                "role": "assistant",
                "content": "world",
                "tokens_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "model": "gpt-5.5",
                "timestamp": "2026-07-14T01:00:03Z",
                "agent_session_id": "sess-1",
                "overwrite_zero_tokens": True,
            },
        ],
        batch_size=10,
    )
    assert saved == 2

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT message_id, role, tokens_used
        FROM daily_messages
        WHERE tool_name = 'codex'
        ORDER BY message_id
        """
    ).fetchall()
    conn.close()

    assert rows == [("assistant-1", "assistant", 0), ("user-1", "user", 1050)]


def test_process_sessions_dir_does_not_reinject_session_total_into_assistant(tmp_path):
    sessions_dir = tmp_path / "sessions" / "2026" / "07" / "14"
    sessions_dir.mkdir(parents=True)
    _write_jsonl(
        sessions_dir / "rollout-test-codex.jsonl",
        [
            {
                "timestamp": "2026-07-14T01:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            },
            {
                "timestamp": "2026-07-14T01:00:01Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-07-14T01:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                },
            },
            {
                "timestamp": "2026-07-14T01:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "world"}],
                },
            },
            {
                "timestamp": "2026-07-14T01:00:04Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1050,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-07-14T01:00:05Z",
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-1"},
            },
        ],
    )

    aggregated = defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )
    all_messages = []
    _process_sessions_dir(
        tmp_path / "sessions",
        "localhost",
        None,
        aggregated,
        all_messages,
        recent=False,
    )

    user_tokens = [m["tokens_used"] for m in all_messages if m["role"] == "user"]
    assistant_tokens = [m["tokens_used"] for m in all_messages if m["role"] == "assistant"]

    assert user_tokens == [1050]
    assert assistant_tokens == [0]
