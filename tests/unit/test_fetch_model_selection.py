#!/usr/bin/env python3
"""Tests for session model selection: most-recently-used, not lexicographic.

Background (issue #241 adversarial review #5): every fetcher derived the model
stored on ``agent_sessions`` without consulting message timestamps:

  * zcode/qwen/codex used ``sorted(stats["models"])[0]``  -> lexicographic *min*
  * claude/openclaw used ``stats["models"][-1]``           -> iteration-order *last*

Both are wrong when a session switches models. The fix routes all five fetchers
through ``shared.utils.update_session_last_seen``, which records the model of
the most recent MODEL-BEARING message (by its own timestamp) — so a modelless
user turn never shadows the model actually in use.

These tests:
  1. Unit-test ``update_session_last_seen`` (the shared fix point) directly.
  2. Integration-test ``fetch_zcode.update_agent_sessions_stats`` end-to-end to
     prove the stored ``agent_sessions.model`` is the most-recently-used model
     and that the existing ``COALESCE`` forward-fix semantics are preserved.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# --------------------------------------------------------------------------- #
# Unit tests for the shared fix point: shared.utils.update_session_last_seen
# --------------------------------------------------------------------------- #


def _new_stats() -> dict:
    """A per-session stats entry shaped like the fetchers' defaultdict factory."""
    return {"last_timestamp": None, "last_model": None}


def test_most_recent_model_wins():
    """Across two messages, the model of the latest timestamp is recorded."""
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", "gpt-4o")
    update_session_last_seen(stats, "2026-06-20T11:00:00", "claude-sonnet-4")
    assert stats["last_model"] == "claude-sonnet-4"
    assert stats["last_timestamp"] == "2026-06-20T11:00:00"


def test_independent_of_input_order():
    """Result must depend on timestamp, NOT iteration order (R6).

    Feed the newest-timestamp message FIRST; last_model must still be the
    newest one. This is the case that list[-1] (claude/openclaw) got wrong.
    """
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T11:00:00", "claude-sonnet-4")
    update_session_last_seen(stats, "2026-06-20T10:00:00", "gpt-4o")
    assert stats["last_model"] == "claude-sonnet-4"


def test_single_model_unchanged():
    """A single-model session resolves to that model (no regression)."""
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", "glm-5")
    assert stats["last_model"] == "glm-5"


def test_modelless_stays_none():
    """Messages with no model leave last_model None (locks the existing guard).

    Note: all selection sites were ALREADY guarded (inline ternary or
    ``if stats["models"]``), so there was no live IndexError to fix here — this
    test documents the post-fix behavior, not a crash repair.
    """
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", None)
    update_session_last_seen(stats, "2026-06-20T11:00:00", None)
    assert stats["last_model"] is None


def test_tie_latest_model_wins():
    """Equal timestamps among MODEL-BEARING messages keep the LAST-seen model.

    Documents the tie rule: for two messages that both carry a model at the same
    timestamp, the later-seen one wins (deterministic). A user/modelless turn at
    the same timestamp never participates — see test_modelless_tie_does_not_win.
    """
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", "first-model")
    update_session_last_seen(stats, "2026-06-20T10:00:00", "second-model")
    assert stats["last_model"] == "second-model"


def test_modelless_tie_does_not_win():
    """A modelless message at timestamp T must NOT shadow a model-bearing
    message at the same T (regression guard for the fetch_zcode fixture pattern
    where a user turn and assistant turn share a timestamp)."""
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    # user turn (no model) first, assistant turn (model) second, same timestamp
    update_session_last_seen(stats, "2026-06-20T10:00:00", None)
    update_session_last_seen(stats, "2026-06-20T10:00:00", "glm-5")
    assert stats["last_model"] == "glm-5"


def test_modelless_newer_does_not_shadow():
    """A modelless message with a NEWER timestamp than the latest model-bearing
    message must not clear or shadow the recorded model.

    This is the pathological case that comparing last_model against the
    all-message last_timestamp would get wrong: the model is tracked against its
    OWN timestamp, so an older model-bearing message survives a newer
    modelless one.
    """
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", "glm-5")
    update_session_last_seen(stats, "2026-06-20T12:00:00", None)  # newer, no model
    assert stats["last_model"] == "glm-5"
    assert stats["last_timestamp"] == "2026-06-20T12:00:00"


def test_falsy_timestamp_ignored():
    """A message with a falsy timestamp must not advance last_model."""
    from shared.utils import update_session_last_seen

    stats = _new_stats()
    update_session_last_seen(stats, "2026-06-20T10:00:00", "glm-5")
    # A later message that carries no timestamp must not overwrite the model.
    update_session_last_seen(stats, None, "gpt-4o")
    update_session_last_seen(stats, "", "gpt-4o")
    assert stats["last_model"] == "glm-5"


# --------------------------------------------------------------------------- #
# Integration: fetch_zcode.update_agent_sessions_stats -> agent_sessions.model
# --------------------------------------------------------------------------- #


def _load_fetch_zcode(tmp_db_url: str):
    """Load scripts/fetch_zcode.py against a temp SQLite DB (mirrors
    tests/unit/test_fetch_zcode.py's loader)."""
    os.environ["DATABASE_URL"] = tmp_db_url
    os.environ["FETCH_USE_SUDO"] = "false"

    from shared import db as db_mod

    importlib.reload(db_mod)

    spec = importlib.util.spec_from_file_location(
        "fetch_zcode_model_sel_under_test", _SCRIPTS_DIR / "fetch_zcode.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _init_dest_schema(db_path: Path) -> None:
    """Minimal destination schema fetch_zcode writes to."""
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

    conn = sqlite3.connect(str(dest_db))
    conn.execute(
        "INSERT INTO users (id, username, system_account, email) VALUES (?, ?, ?, ?)",
        (1, "rhuang", "rhuang", "rhuang@localhost"),
    )
    conn.commit()
    conn.close()
    return mod


def _msg(
    msg_id: str,
    role: str,
    model: str | None,
    timestamp: str,
    session_id: str = "sess_models",
) -> dict:
    """Build a fetch-shaped message dict."""
    return {
        "date": "2026-06-20",
        "tool_name": "zcode",
        "host_name": "localhost",
        "message_id": msg_id,
        "role": role,
        "content": "x",
        "content_blocks": None,
        "tokens_used": 10,
        "input_tokens": 6,
        "output_tokens": 4,
        "model": model,
        "timestamp": timestamp,
        "sender_name": "rhuang-host-zcode",
        "agent_session_id": session_id,
        "project_path": "/repo",
    }


def _get_model(dest_db: Path, session_id: str):
    conn = sqlite3.connect(str(dest_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT model FROM agent_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row["model"] if row else None


def test_integration_multi_model_picks_most_recent(fetch_mod, tmp_path):
    """Most-recently-used model is stored, not lexicographic min/max.

    gpt-4o used early, claude-sonnet-4 used later -> stored model must be
    claude-sonnet-4 (NOT sorted()[0]='claude-sonnet-4' by luck, NOT MAX='gpt-4o').
    Picked so the answer differs from BOTH the old min and MAX heuristics.
    """
    messages = [
        _msg("m1", "user", None, "2026-06-20T09:00:00"),
        _msg("m2", "assistant", "gpt-4o", "2026-06-20T10:00:00"),
        _msg("m3", "assistant", "claude-sonnet-4", "2026-06-20T11:00:00"),
    ]
    fetch_mod.update_agent_sessions_stats(messages)
    assert _get_model(tmp_path / "dest.sqlite", "sess_models") == "claude-sonnet-4"


def test_integration_independent_of_input_order(fetch_mod, tmp_path):
    """Newest-timestamp message fed FIRST still wins (proves R6 end-to-end)."""
    messages = [
        _msg("m3", "assistant", "claude-sonnet-4", "2026-06-20T11:00:00"),
        _msg("m2", "assistant", "gpt-4o", "2026-06-20T10:00:00"),
    ]
    fetch_mod.update_agent_sessions_stats(messages)
    assert _get_model(tmp_path / "dest.sqlite", "sess_models") == "claude-sonnet-4"


def test_integration_update_preserves_existing_model(fetch_mod, tmp_path):
    """COALESCE forward-fix: a pre-existing non-NULL model is preserved on
    re-fetch (historical backfill is an explicit, separate step)."""
    dest_db = tmp_path / "dest.sqlite"
    conn = sqlite3.connect(str(dest_db))
    conn.execute(
        "INSERT INTO agent_sessions (session_id, tool_name, model, status, host_name) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_models", "zcode", "manual-model", "completed", "localhost"),
    )
    conn.commit()
    conn.close()

    fetch_mod.update_agent_sessions_stats(
        [_msg("m1", "assistant", "claude-sonnet-4", "2026-06-20T11:00:00")]
    )
    assert _get_model(dest_db, "sess_models") == "manual-model"


def test_integration_modelless_session_model_is_null(fetch_mod, tmp_path):
    """A session whose messages carry no model stores NULL (None)."""
    fetch_mod.update_agent_sessions_stats(
        [
            _msg("m1", "user", None, "2026-06-20T09:00:00"),
            _msg("m2", "assistant", None, "2026-06-20T10:00:00"),
        ]
    )
    assert _get_model(tmp_path / "dest.sqlite", "sess_models") is None


def test_integration_single_model(fetch_mod, tmp_path):
    """Sanity: a single-model session stores that model."""
    fetch_mod.update_agent_sessions_stats([_msg("m1", "assistant", "glm-5", "2026-06-20T10:00:00")])
    assert _get_model(tmp_path / "dest.sqlite", "sess_models") == "glm-5"
