from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from flask import Flask, g

from app.modules.workspace.session_manager import AgentSession, SessionMessage
from app.routes import workspace as workspace_route


class _FakeListDatabase:
    def __init__(self, session_rows: list[dict]):
        self.session_rows = session_rows

    def fetch_one(self, query: str, params):
        if "COUNT(*) as count FROM agent_sessions" in query:
            rows = self._visible_rows(query)
            return {"count": len(rows)}
        raise AssertionError(f"Unexpected fetch_one query: {query}")

    def fetch_all(self, query: str, params):
        if "SELECT * FROM agent_sessions" in query:
            return self._visible_rows(query)
        if "FROM session_messages" in query and "role = 'user'" in query:
            return [
                {"session_id": row["session_id"], "content": "first prompt"}
                for row in self.session_rows
            ]
        raise AssertionError(f"Unexpected fetch_all query: {query}")

    def _visible_rows(self, query: str) -> list[dict]:
        if "COALESCE(cli_session_id, '') != ''" not in query:
            return self.session_rows
        return [
            row
            for row in self.session_rows
            if not (
                row.get("session_type") == "workflow"
                and row.get("cli_session_id")
                and '"workflow_id"' in (row.get("context") or "")
            )
        ]


def _make_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_list_sessions_uses_agent_sessions_summary(monkeypatch):
    session_row = {
        "id": 1,
        "session_id": "sess-1125",
        "session_type": "chat",
        "title": "Claude Session",
        "tool_name": "claude",
        "host_name": "localhost",
        "user_id": 7,
        "status": "completed",
        "total_tokens": 1660000,
        "total_input_tokens": 1200000,
        "total_output_tokens": 460000,
        "message_count": 430,
        "request_count": 416,
        "model": "claude-sonnet",
        "created_at": datetime(2026, 6, 17, 23, 28, 31),
        "updated_at": datetime(2026, 6, 18, 1, 59, 0),
        "completed_at": None,
        "expires_at": None,
        "project_path": "/tmp/project",
        "workspace_type": "local",
        "remote_machine_id": None,
    }
    fake_db = _FakeListDatabase([session_row])

    monkeypatch.setattr("app.repositories.database.Database", lambda: fake_db)
    monkeypatch.setattr("app.repositories.database.adapt_sql", lambda sql: sql)
    monkeypatch.setattr("app.repositories.database.escape_like", lambda value: value)
    monkeypatch.setattr("app.repositories.database.get_param_placeholder", lambda: "?")
    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

    app = _make_app()
    with app.test_request_context("/api/workspace/sessions?page=1&limit=20"):
        g.user = {"id": 7, "role": "user"}
        response = workspace_route.list_sessions()

    payload = response.get_json()
    session = payload["data"]["sessions"][0]
    assert session["request_count"] == 416
    assert session["message_count"] == 430
    assert session["total_tokens"] == 1660000
    assert session["first_message"] == "first prompt"


def test_list_sessions_hides_autonomous_tracking_wrappers(monkeypatch):
    tracking_row = {
        "id": 1,
        "session_id": "track-123",
        "session_type": "workflow",
        "title": "Autonomous: wf-1",
        "tool_name": "claude",
        "host_name": "localhost",
        "user_id": 7,
        "status": "completed",
        "total_tokens": 120,
        "total_input_tokens": 80,
        "total_output_tokens": 40,
        "message_count": 2,
        "request_count": 1,
        "model": "claude-sonnet",
        "created_at": datetime(2026, 6, 18, 0, 0, 0),
        "updated_at": datetime(2026, 6, 18, 0, 5, 0),
        "completed_at": None,
        "expires_at": None,
        "project_path": "/tmp/project",
        "workspace_type": "local",
        "remote_machine_id": None,
        "cli_session_id": "actual-claude-123",
        "context": '{"workflow_id": "wf-1"}',
    }
    provider_row = {
        **tracking_row,
        "id": 2,
        "session_id": "actual-claude-123",
        "session_type": "chat",
        "title": "claude - actual",
        "cli_session_id": "",
    }
    fake_db = _FakeListDatabase([tracking_row, provider_row])

    monkeypatch.setattr("app.repositories.database.Database", lambda: fake_db)
    monkeypatch.setattr("app.repositories.database.adapt_sql", lambda sql: sql)
    monkeypatch.setattr("app.repositories.database.escape_like", lambda value: value)
    monkeypatch.setattr("app.repositories.database.get_param_placeholder", lambda: "?")
    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

    app = _make_app()
    with app.test_request_context("/api/workspace/sessions?page=1&limit=20"):
        g.user = {"id": 7, "role": "user"}
        response = workspace_route.list_sessions()

    payload = response.get_json()
    sessions = payload["data"]["sessions"]
    assert [session["session_id"] for session in sessions] == ["actual-claude-123"]


def test_get_session_keeps_agent_sessions_request_count(monkeypatch):
    session = AgentSession(
        session_id="sess-1125-detail",
        tool_name="claude",
        user_id=7,
        request_count=14,
        message_count=27,
        total_tokens=2048,
        total_input_tokens=1500,
        total_output_tokens=548,
        messages=[
            SessionMessage(session_id="sess-1125-detail", role="assistant", content="turn 1"),
            SessionMessage(session_id="sess-1125-detail", role="assistant", content="turn 2"),
        ],
    )
    manager = SimpleNamespace(
        get_session=lambda session_id, include_messages=False: session,
        get_messages_page=lambda session_id, limit=None, milestone_id=None: {
            "messages": session.messages,
            "has_more": False,
            "next_cursor": None,
        },
    )

    monkeypatch.setattr(workspace_route, "get_session_manager", lambda: manager)
    monkeypatch.setattr("app.repositories.database.Database", lambda: SimpleNamespace())

    app = _make_app()
    with app.test_request_context("/api/workspace/sessions/sess-1125-detail?include_messages=true"):
        g.user = {"id": 7, "role": "user"}
        response = workspace_route.get_session("sess-1125-detail")

    payload = response.get_json()
    data = payload["data"]
    assert data["request_count"] == 14
    assert len(data["messages"]) == 2


def test_get_session_does_not_fallback_to_daily_messages(monkeypatch):
    manager = SimpleNamespace(get_session=lambda session_id, include_messages=False: None)

    monkeypatch.setattr(workspace_route, "get_session_manager", lambda: manager)

    app = _make_app()
    with app.test_request_context("/api/workspace/sessions/missing?include_messages=true"):
        g.user = {"id": 7, "role": "admin"}
        response, status_code = workspace_route.get_session("missing")

    payload = response.get_json()
    assert status_code == 404
    assert payload["error"] == "Session not found"
