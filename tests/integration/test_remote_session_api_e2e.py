"""API-layer transcript contract for remote sessions (#2047).

The ``GET /api/remote/sessions/<id>`` route returns ``RemoteSessionManager
.get_session_status()``, whose ``messages`` field is the reconnect-replay
transcript (``SessionManager.get_messages``) and whose ``message_count`` is the
persisted ``agent_sessions.message_count``. These tests bind
``RemoteSessionManager`` to a real SQLite ``SessionManager`` and assert that the
status payload produced by a real streaming turn matches the persisted rows —
closing the DB → API contract gap without a live deployment.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.remote_session_manager import RemoteSessionManager
from app.modules.workspace.session_manager import SessionManager, SessionType


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    import app.repositories.database as db_mod

    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "remote_api.db"))
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


def _assistant(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


def _result() -> str:
    return json.dumps({"type": "result", "subtype": "success"})


def test_session_status_messages_match_db_rows(manager, sqlite_sm):
    """``get_session_status`` exposes the persisted transcript for replay."""
    sqlite_sm.create_session(
        tool_name="claude",
        session_id="sess-api",
        session_type=SessionType.CHAT.value,
        user_id=1,
    )
    manager.process_session_output("sess-api", _assistant("API round trip"))
    manager.process_session_output("sess-api", _result())

    status = manager.get_session_status("sess-api")
    assert status is not None
    assert status["message_count"] == 1
    assert len(status["messages"]) == 1
    msg = status["messages"][0]
    assert msg.role == "assistant"
    assert msg.content == "API round trip"
    assert msg.source == "remote_live"


def test_session_status_no_empty_bubble_for_tool_only_turn(manager, sqlite_sm):
    """Interactive tool-only turn produces no row, so the API exposes none.

    Guards the #1939 regression fixed by #2047 at the API surface: a reconnect
    after a tool-only turn must not surface an empty assistant bubble.
    """
    sqlite_sm.create_session(
        tool_name="claude",
        session_id="sess-api2",
        session_type=SessionType.CHAT.value,
        user_id=1,
    )
    manager.process_session_output(
        "sess-api2",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]
                },
            }
        ),
    )
    manager.process_session_output("sess-api2", _result())

    status = manager.get_session_status("sess-api2")
    assert status["message_count"] == 0
    assert status["messages"] == []


def test_session_status_message_count_reflects_persisted_turns(manager, sqlite_sm):
    """``message_count`` equals the number of inserted assistant turns."""
    sqlite_sm.create_session(
        tool_name="claude",
        session_id="sess-api3",
        session_type=SessionType.CHAT.value,
        user_id=1,
    )
    for text in ("one", "two"):
        manager.process_session_output("sess-api3", _assistant(text))
        manager.process_session_output("sess-api3", _result())

    status = manager.get_session_status("sess-api3")
    assert status["message_count"] == 2
    assert [m.content for m in status["messages"]] == ["one", "two"]
