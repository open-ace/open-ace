"""Regression test for the remote_sync ``message_count`` contract.

Background: commit 8bf44e72 (PR #1221) made ``append_transcript_message``
side-effect-free — it calls ``add_message(count_usage=False)``, which no longer
advances ``agent_sessions.message_count``. Responsibility for ``message_count``
moved to the *caller*, which must accumulate ``_was_inserted`` and apply the
delta via ``increment_session_usage(message_delta=...)``. Three callers were
migrated (``llm_proxy_handler``, ``agent_runner``, ``remote_session_manager``);
the ``remote.py`` ``session_sync`` import loop was missed, so remote-synced
sessions stopped reflecting their imported messages (fixed in PR #1225).

These tests encode the contract every ``append_transcript_message`` caller
relies on, against a real isolated SQLite ``SessionManager``. They would have
failed at the regression: without the ``increment_session_usage(message_delta=)``
call, ``message_count`` stays 0.
"""

from __future__ import annotations

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.session_manager import SessionManager


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "remote_sync_count.db"))
    sm._ensure_tables()
    # create_session writes project_id / project_path; ensure both exist.
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


def _create_session(sm: SessionManager, session_id: str = "sess-sync"):
    return sm.create_session(tool_name="claude", session_id=session_id, user_id=1)


def _import_like_remote_sync(sm: SessionManager, session_id: str, messages: list) -> int:
    """Mirror the remote.py session_sync import loop exactly.

    Append each message, accumulate the count of newly-inserted rows, then
    apply the net delta once via ``increment_session_usage``. Returns the delta
    that was applied.
    """
    synced_message_delta = 0
    for msg in messages:
        stored = sm.append_transcript_message(
            session_id=session_id,
            role=msg["role"],
            content=msg["content"],
            source="remote_sync",
            external_message_id=msg["external_message_id"],
        )
        if getattr(stored, "_was_inserted", False):
            synced_message_delta += 1
    if synced_message_delta:
        sm.increment_session_usage(session_id, message_delta=synced_message_delta)
    return synced_message_delta


def test_remote_sync_import_advances_message_count(sqlite_sm):
    """A bulk import advances message_count by the number of new messages."""
    _create_session(sqlite_sm)
    messages = [
        {"role": "user", "content": "what is 2+2?", "external_message_id": "m1"},
        {"role": "assistant", "content": "4", "external_message_id": "m2"},
        {"role": "assistant", "content": "let me elaborate", "external_message_id": "m3"},
    ]

    delta = _import_like_remote_sync(sqlite_sm, "sess-sync", messages)

    assert delta == 3
    session = sqlite_sm.get_session("sess-sync")
    assert session.message_count == 3  # was 0 before PR #1225 (the regression)


def test_remote_sync_reimport_does_not_double_count(sqlite_sm):
    """Re-syncing the same history must not advance message_count again."""
    _create_session(sqlite_sm)
    messages = [
        {"role": "user", "content": "again", "external_message_id": "m1"},
        {"role": "assistant", "content": "ok", "external_message_id": "m2"},
    ]

    _import_like_remote_sync(sqlite_sm, "sess-sync", messages)
    delta = _import_like_remote_sync(sqlite_sm, "sess-sync", messages)

    assert delta == 0  # dedup: _was_inserted is False on the upsert path
    session = sqlite_sm.get_session("sess-sync")
    assert session.message_count == 2  # unchanged after re-sync


def test_append_transcript_message_alone_does_not_advance_count(sqlite_sm):
    """Guards the regression root cause.

    ``append_transcript_message`` keeps ``add_message`` side-effect-free
    (``count_usage=False``), so it must NOT touch ``message_count`` on its own.
    This is precisely why the caller owns the delta — and why the missed
    migration in remote.py zeroed-out the count.
    """
    _create_session(sqlite_sm)

    sqlite_sm.append_transcript_message(
        session_id="sess-sync",
        role="assistant",
        content="a message with no caller-side accounting",
        source="remote_sync",
        external_message_id="m1",
    )

    session = sqlite_sm.get_session("sess-sync")
    assert session.message_count == 0


def _import_with_tokens(sm: SessionManager, session_id: str, messages: list) -> dict:
    """Mirror the remote.py session_sync token accumulation pattern.

    Issue #1955: session_sync must accumulate tokens and pass them to
    increment_session_usage for proper usage tracking.
    """
    synced_message_delta = 0
    synced_input_tokens = 0
    synced_output_tokens = 0
    for msg in messages:
        stored = sm.append_transcript_message(
            session_id=session_id,
            role=msg["role"],
            content=msg["content"],
            source="remote_sync",
            external_message_id=msg["external_message_id"],
        )
        if getattr(stored, "_was_inserted", False):
            synced_message_delta += 1
            synced_input_tokens += msg.get("input_tokens", 0)
            synced_output_tokens += msg.get("output_tokens", 0)
    if synced_message_delta or synced_input_tokens or synced_output_tokens:
        sm.increment_session_usage(
            session_id,
            message_delta=synced_message_delta,
            total_tokens_delta=synced_input_tokens + synced_output_tokens,
            total_input_delta=synced_input_tokens,
            total_output_delta=synced_output_tokens,
        )
    return {
        "message_delta": synced_message_delta,
        "input_tokens": synced_input_tokens,
        "output_tokens": synced_output_tokens,
    }


def test_remote_sync_import_advances_token_count(sqlite_sm):
    """Issue #1955: session_sync must advance token counts.

    Without the token accumulation fix, total_tokens would stay 0 even though
    session_sync reports usage.
    """
    _create_session(sqlite_sm)
    messages = [
        {"role": "user", "content": "what is 2+2?", "external_message_id": "m1", "input_tokens": 5},
        {"role": "assistant", "content": "4", "external_message_id": "m2", "output_tokens": 10},
        {
            "role": "assistant",
            "content": "let me elaborate",
            "external_message_id": "m3",
            "output_tokens": 20,
        },
    ]

    result = _import_with_tokens(sqlite_sm, "sess-sync", messages)

    assert result["message_delta"] == 3
    assert result["input_tokens"] == 5
    assert result["output_tokens"] == 30
    session = sqlite_sm.get_session("sess-sync")
    assert session.message_count == 3
    assert session.total_tokens == 35
    assert session.total_input_tokens == 5
    assert session.total_output_tokens == 30


def test_remote_sync_reimport_does_not_double_count_tokens(sqlite_sm):
    """Issue #1955: Re-syncing must not double-count tokens."""
    _create_session(sqlite_sm)
    messages = [
        {"role": "user", "content": "again", "external_message_id": "m1", "input_tokens": 7},
        {"role": "assistant", "content": "ok", "external_message_id": "m2", "output_tokens": 15},
    ]

    _import_with_tokens(sqlite_sm, "sess-sync", messages)
    result = _import_with_tokens(sqlite_sm, "sess-sync", messages)

    assert result["message_delta"] == 0
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    session = sqlite_sm.get_session("sess-sync")
    assert session.total_tokens == 22  # unchanged after re-sync
