"""End-to-end transcript contract characterization for remote sessions (#2047).

These tests drive the real ``RemoteSessionManager.process_session_output``
streaming path against a real isolated SQLite ``SessionManager`` and assert on
actual ``session_messages`` / ``agent_sessions`` / ``daily_messages`` rows —
not ``MagicMock`` call args. They pin the transcript / content_blocks /
message-count / replay contract for ordinary interactive remote sessions and
autonomous workflow sessions.

Background: PR #1939 widened the shared ``_accumulate_assistant_text`` /
``_flush_assistant_buffer`` path so tool-only turns wrote an empty
``content=""`` assistant row and folded Claude ``user`` ``tool_result`` blocks
into the assistant turn. That behaviour is correct for autonomous workflow
sessions (which need structured command/test evidence) but was a regression
for ordinary interactive remote sessions. #2047 scopes the evidence policy to
autonomous sessions via ``is_autonomous_workflow_session`` so the shared path
is additive rather than a silent change to interactive semantics.

Contract locked here (Phase A baseline):

* Ordinary interactive sessions persist a turn only when it produced visible
  assistant text; tool/thinking-only turns write no row and do not inflate
  ``message_count``; ``user`` ``tool_result`` blocks are ignored.
* Autonomous workflow sessions additionally persist tool/thinking-only turns
  as ``content_blocks`` evidence and fold ``user`` ``tool_result`` blocks into
  the turn, retaining the #1939 evidence policy.
* ``result`` then ``is_complete`` does not double-flush; the same turn replayed
  does not duplicate rows.
* Reconnect replay (``get_messages``) returns rows in ``timestamp ASC`` order.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.remote_session_manager import RemoteSessionManager
from app.modules.workspace.session_manager import SessionManager, SessionType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    """A real isolated SQLite ``SessionManager`` with tables created."""
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "remote_transcript.db"))
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


@pytest.fixture
def manager(sqlite_sm):
    """A ``RemoteSessionManager`` wired to the real ``SessionManager``.

    The agent manager, API-key proxy, run recorder and policy evaluator are
    replaced with no-op doubles so the streaming-persistence path is exercised
    without external services.
    """
    RemoteSessionManager._assistant_text_buffer.clear()
    RemoteSessionManager._content_blocks_buffer.clear()

    with (
        patch(
            "app.modules.workspace.remote_session_manager.get_remote_agent_manager",
            return_value=MagicMock(),
        ),
        patch("app.modules.workspace.remote_session_manager.APIKeyProxyService"),
        patch(
            "app.modules.workspace.remote_session_manager.get_run_recorder",
            return_value=MagicMock(),
        ),
        patch(
            "app.modules.workspace.remote_session_manager.get_evaluator",
            return_value=MagicMock(),
        ),
    ):
        mgr = RemoteSessionManager()
    mgr._session_manager = sqlite_sm
    return mgr


def _create_interactive_session(sm: SessionManager, session_id: str = "sess-interactive"):
    return sm.create_session(
        tool_name="claude",
        session_id=session_id,
        session_type=SessionType.CHAT.value,
        user_id=1,
    )


def _create_autonomous_session(sm: SessionManager, session_id: str = "sess-autonomous"):
    return sm.create_session(
        tool_name="claude",
        session_id=session_id,
        session_type=SessionType.WORKFLOW.value,
        context={"workflow_id": "wf-test-1"},
        user_id=1,
    )


def _assistant(msg_json: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": msg_json["content"]}})


def _result() -> str:
    return json.dumps({"type": "result", "subtype": "success"})


def _messages(sm: SessionManager, session_id: str):
    return sm.get_messages(session_id)


def _message_count(sm: SessionManager, session_id: str) -> int:
    session = sm.get_session(session_id)
    return getattr(session, "message_count", 0) or 0


# ---------------------------------------------------------------------------
# Ordinary interactive remote session — Phase A baseline
# ---------------------------------------------------------------------------


def test_assistant_text_turn_round_trips_db(manager, sqlite_sm):
    """A text-only assistant turn persists one row with the visible text."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        _assistant({"content": [{"type": "text", "text": "Hello world"}]}),
    )
    manager.process_session_output("sess-interactive", _result())

    msgs = _messages(sqlite_sm, "sess-interactive")
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    assert msgs[0].content == "Hello world"
    assert msgs[0].source == "remote_live"
    assert _message_count(sqlite_sm, "sess-interactive") == 1


def test_text_plus_tool_use_turn_persists_blocks(manager, sqlite_sm):
    """A turn with visible text keeps tool_use blocks in metadata for context."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        _assistant(
            {
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {}},
                ]
            }
        ),
    )
    manager.process_session_output("sess-interactive", _result())

    msgs = _messages(sqlite_sm, "sess-interactive")
    assert len(msgs) == 1
    assert msgs[0].content == "Let me check."
    blocks = msgs[0].metadata.get("content_blocks", [])
    assert any(b.get("type") == "tool_use" for b in blocks)


def test_tool_only_turn_writes_no_row_and_no_count_inflation(manager, sqlite_sm):
    """Interactive tool-only turn writes no row and does not inflate count.

    This is the #1939 regression fixed by #2047: previously such a turn wrote
    an empty ``content=""`` assistant bubble and bumped ``message_count``.
    """
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        _assistant({"content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]}),
    )
    manager.process_session_output("sess-interactive", _result())

    assert _messages(sqlite_sm, "sess-interactive") == []
    assert _message_count(sqlite_sm, "sess-interactive") == 0


def test_thinking_only_turn_writes_no_row(manager, sqlite_sm):
    """Interactive thinking-only turn writes no row."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        _assistant({"content": [{"type": "thinking", "thinking": "pondering"}]}),
    )
    manager.process_session_output("sess-interactive", _result())

    assert _messages(sqlite_sm, "sess-interactive") == []


def test_interactive_user_tool_result_ignored(manager, sqlite_sm):
    """Interactive sessions do not fold user tool_result into the transcript."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu1",
                            "content": "9 passed",
                            "is_error": False,
                        }
                    ]
                },
            }
        ),
    )
    manager.process_session_output("sess-interactive", _result())

    assert _messages(sqlite_sm, "sess-interactive") == []


# ---------------------------------------------------------------------------
# Autonomous workflow remote session — evidence policy (additive)
# ---------------------------------------------------------------------------


def test_autonomous_tool_only_turn_persists_evidence(manager, sqlite_sm):
    """Autonomous workflow sessions keep tool-only turns as structured evidence."""
    _create_autonomous_session(sqlite_sm)
    manager.process_session_output(
        "sess-autonomous",
        _assistant({"content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]}),
    )
    manager.process_session_output("sess-autonomous", _result())

    msgs = _messages(sqlite_sm, "sess-autonomous")
    assert len(msgs) == 1
    assert msgs[0].content == ""
    blocks = msgs[0].metadata.get("content_blocks", [])
    assert blocks and blocks[0].get("type") == "tool_use"
    assert _message_count(sqlite_sm, "sess-autonomous") == 1


def test_autonomous_user_tool_result_folded_into_turn(manager, sqlite_sm):
    """Autonomous sessions fold Claude user tool_result into the assistant turn."""
    _create_autonomous_session(sqlite_sm)
    manager.process_session_output(
        "sess-autonomous",
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu1",
                            "content": "9 passed in 0.8s",
                            "is_error": False,
                        }
                    ]
                },
            }
        ),
    )
    manager.process_session_output("sess-autonomous", _result())

    msgs = _messages(sqlite_sm, "sess-autonomous")
    assert len(msgs) == 1
    blocks = msgs[0].metadata.get("content_blocks", [])
    assert any(b.get("type") == "tool_result" and b.get("tool_use_id") == "tu1" for b in blocks)


def test_autonomous_thinking_only_turn_persists_evidence(manager, sqlite_sm):
    """Autonomous sessions keep thinking-only turns as evidence."""
    _create_autonomous_session(sqlite_sm)
    manager.process_session_output(
        "sess-autonomous",
        _assistant({"content": [{"type": "thinking", "thinking": "pondering"}]}),
    )
    manager.process_session_output("sess-autonomous", _result())

    msgs = _messages(sqlite_sm, "sess-autonomous")
    assert len(msgs) == 1
    blocks = msgs[0].metadata.get("content_blocks", [])
    assert any(b.get("type") == "thinking" for b in blocks)


# ---------------------------------------------------------------------------
# Idempotency / replay contract
# ---------------------------------------------------------------------------


def test_result_then_process_complete_does_not_double_flush(manager, sqlite_sm):
    """``result`` flushes the turn; a subsequent ``is_complete`` writes nothing extra."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        _assistant({"content": [{"type": "text", "text": "Once only"}]}),
    )
    manager.process_session_output("sess-interactive", _result())
    # Process exit arrives after the result event with empty data.
    manager.process_session_output("sess-interactive", "", is_complete=True)

    msgs = _messages(sqlite_sm, "sess-interactive")
    assert len(msgs) == 1
    assert _message_count(sqlite_sm, "sess-interactive") == 1


def test_repeated_turns_do_not_duplicate_rows(manager, sqlite_sm):
    """Two distinct turns produce two distinct rows; replaying a turn does not dup."""
    _create_interactive_session(sqlite_sm)
    for text in ("First reply", "Second reply"):
        manager.process_session_output(
            "sess-interactive", _assistant({"content": [{"type": "text", "text": text}]})
        )
        manager.process_session_output("sess-interactive", _result())

    msgs = _messages(sqlite_sm, "sess-interactive")
    assert [m.content for m in msgs] == ["First reply", "Second reply"]
    assert _message_count(sqlite_sm, "sess-interactive") == 2


def test_reconnect_replay_matches_live_order(manager, sqlite_sm):
    """``get_messages`` returns rows in timestamp ASC (write) order on reconnect."""
    _create_interactive_session(sqlite_sm)
    for text in ("alpha", "beta", "gamma"):
        manager.process_session_output(
            "sess-interactive", _assistant({"content": [{"type": "text", "text": text}]})
        )
        manager.process_session_output("sess-interactive", _result())

    replayed = [m.content for m in _messages(sqlite_sm, "sess-interactive")]
    assert replayed == ["alpha", "beta", "gamma"]


def test_openai_message_format_round_trips(manager, sqlite_sm):
    """The OpenAI-compatible ``message`` shape is accumulated and persisted."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive",
        json.dumps({"type": "message", "role": "assistant", "content": "OpenAI text"}),
    )
    manager.process_session_output("sess-interactive", _result())

    msgs = _messages(sqlite_sm, "sess-interactive")
    assert len(msgs) == 1
    assert msgs[0].content == "OpenAI text"


def test_system_stream_message_persisted(manager, sqlite_sm):
    """A ``system`` stream completion message is stored as a system row."""
    _create_interactive_session(sqlite_sm)
    manager.process_session_output(
        "sess-interactive", "agent exited", stream="system", is_complete=True
    )

    msgs = _messages(sqlite_sm, "sess-interactive")
    system_msgs = [m for m in msgs if m.role == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0].content == "agent exited"
