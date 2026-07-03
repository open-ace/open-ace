#!/usr/bin/env python3
"""
Route-layer integration tests for Issue #241 (#22) message pagination.

These exercise the actual view functions ``get_session`` and
``get_session_messages`` in app/routes/workspace.py — ownership gating,
session-404, cursor extraction, milestone-aware total, and an end-to-end page
walk through the HTTP boundary — by invoking the views directly inside a Flask
request context with ``g.user`` preset and ``get_session_manager`` patched to
an isolated temp-DB-backed manager. This covers the route glue the unit tests
in test_session_messages_pagination.py leave aside, without standing up the
full auth/token machinery (the view is called past its before_request guard).
"""

import os
import sys
import tempfile
from datetime import datetime

import pytest
from flask import Flask, g

# Make the project root importable when run directly.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import app.modules.workspace.session_manager as sm_module  # noqa: E402
import app.routes.workspace as workspace_route  # noqa: E402
from app.modules.workspace.session_manager import SessionManager  # noqa: E402


@pytest.fixture
def app_and_manager(monkeypatch):
    """A minimal Flask app + an isolated temp-DB SessionManager wired in.

    Forces SQLite (the sandbox otherwise defaults to PostgreSQL) and patches
    ``get_session_manager`` on the workspace route module so every view call in
    this test uses the throwaway DB.
    """
    monkeypatch.setattr(sm_module, "is_postgresql", lambda *a, **k: False)

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SessionManager(db_path=path)
    mgr._ensure_tables()
    monkeypatch.setattr(workspace_route, "get_session_manager", lambda: mgr)

    app = Flask(__name__)
    try:
        yield app, mgr
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _make_session(mgr, user_id=1, session_id="sess-route"):
    s = mgr.create_session(
        tool_name="qwen",
        user_id=user_id,
        session_type="chat",
        title="route test",
        session_id=session_id,
    )
    return s.session_id


def _add(mgr, sid, n, milestone_id="", count_usage=True):
    mgr.add_message(
        sid,
        role="user",
        content=f"msg {n}",
        tokens_used=10,
        model="glm-5",
        milestone_id=milestone_id,
        count_usage=count_usage,
        timestamp=datetime(2026, 7, 4, 12, n // 60, n % 60),
    )


def _status(resp):
    """A view may return either a Response (default 200) or a (Response, code)
    tuple (denial paths). Normalize to the HTTP status code."""
    if isinstance(resp, tuple):
        return resp[1]
    return resp.status_code


def _json(resp):
    """Extract the JSON body from a view return value."""
    body = resp[0] if isinstance(resp, tuple) else resp
    return body.get_json()


# =========================== Ownership gating ================================


class TestOwnershipAnd404:
    def test_unknown_session_returns_404(self, app_and_manager):
        app, _ = app_and_manager
        with app.test_request_context("/api/workspace/sessions/does-not-exist"):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session("does-not-exist")
        assert _status(resp) == 404

    def test_non_owner_gets_403(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        with app.test_request_context(f"/api/workspace/sessions/{sid}"):
            g.user = {"id": 999, "role": "user"}  # different user
            resp = workspace_route.get_session(sid)
        assert _status(resp) == 403

    def test_messages_endpoint_enforces_ownership_before_load(self, app_and_manager):
        """Ownership must be checked on the session BEFORE touching messages."""
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        _add(mgr, sid, 0)
        with app.test_request_context(f"/api/workspace/sessions/{sid}/messages"):
            g.user = {"id": 999, "role": "user"}
            resp = workspace_route.get_session_messages(sid)
        assert _status(resp) == 403

    def test_messages_endpoint_404_for_unknown_session(self, app_and_manager):
        app, _ = app_and_manager
        with app.test_request_context("/api/workspace/sessions/ghost/messages"):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session_messages("ghost")
        assert _status(resp) == 404

    def test_admin_bypasses_owner_check(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        with app.test_request_context(f"/api/workspace/sessions/{sid}"):
            g.user = {"id": 2, "role": "admin"}  # admin, different id
            resp = workspace_route.get_session(sid)
        assert _status(resp) == 200


# =================== get_session pagination envelope ========================


class TestGetSessionEnvelope:
    def test_include_messages_returns_recent_page_and_metadata(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        for n in range(5):
            _add(mgr, sid, n)

        with app.test_request_context(
            f"/api/workspace/sessions/{sid}?include_messages=true&message_limit=2"
        ):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session(sid)

        assert _status(resp) == 200
        data = _json(resp)["data"]
        assert len(data["messages"]) == 2  # capped to message_limit
        assert data["messages_has_more"] is True
        assert data["messages_next_cursor"] is not None
        assert {"timestamp", "id"} == set(data["messages_next_cursor"])
        # session-level message_count (count_usage=True incremented it).
        assert data["messages_total"] == 5

    def test_without_include_messages_has_no_pagination_envelope(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        _add(mgr, sid, 0)
        with app.test_request_context(f"/api/workspace/sessions/{sid}"):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session(sid)
        data = _json(resp)["data"]
        assert "messages_total" not in data
        assert "messages_next_cursor" not in data

    def test_milestone_total_uses_conditional_count(self, app_and_manager):
        """With a milestone filter, total must come from count_messages, not the
        session-level message_count (which isn't milestone-aware)."""
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        for n in range(3):
            _add(mgr, sid, n, milestone_id="M1")
        for n in range(3, 5):
            _add(mgr, sid, n, milestone_id="M2")

        with app.test_request_context(
            f"/api/workspace/sessions/{sid}?include_messages=true&milestone_id=M1"
        ):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session(sid)
        data = _json(resp)["data"]
        # session.message_count == 5, but milestone M1 has only 3.
        assert data["messages_total"] == 3
        assert all(m["milestone_id"] == "M1" for m in data["messages"])


# =================== /messages endpoint cursor walk ========================


class TestMessagesEndpointWalk:
    def test_cursor_walk_through_endpoint(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        for n in range(7):
            _add(mgr, sid, n, count_usage=False)

        seen_ids = []
        before_ts = before_id = None
        while True:
            qs = f"/api/workspace/sessions/{sid}/messages?limit=3"
            if before_ts is not None:
                qs += f"&before_timestamp={before_ts}&before_id={before_id}"
            with app.test_request_context(qs):
                g.user = {"id": 1, "role": "user"}
                resp = workspace_route.get_session_messages(sid)
            assert _status(resp) == 200
            payload = _json(resp)["data"]
            seen_ids.extend(m["id"] for m in payload["messages"])
            if not payload["has_more"]:
                break
            cursor = payload["next_cursor"]
            before_ts, before_id = cursor["timestamp"], cursor["id"]

        assert sorted(seen_ids) == sorted(
            {m.id for m in mgr.get_session(sid, include_messages=True).messages}
        )
        assert len(seen_ids) == len(set(seen_ids))  # no dupes across pages

    def test_lone_before_timestamp_is_ignored(self, app_and_manager):
        """A cursor is only valid when BOTH parts are present; a lone
        before_timestamp must be treated as no cursor (most-recent page)."""
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        for n in range(5):
            _add(mgr, sid, n, count_usage=False)

        with app.test_request_context(
            f"/api/workspace/sessions/{sid}/messages?before_timestamp=2026-07-04T12:00:03"
        ):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session_messages(sid)
        data = _json(resp)["data"]
        # No before_id => cursor dropped => most-recent page (5 rows, has_more False).
        assert len(data["messages"]) == 5
        assert data["has_more"] is False

    def test_limit_clamped_to_max_via_endpoint(self, app_and_manager):
        app, mgr = app_and_manager
        sid = _make_session(mgr, user_id=1)
        for n in range(3):
            _add(mgr, sid, n, count_usage=False)

        with app.test_request_context(f"/api/workspace/sessions/{sid}/messages?limit=99999"):
            g.user = {"id": 1, "role": "user"}
            resp = workspace_route.get_session_messages(sid)
        assert _status(resp) == 200
        # Clamped to MAX (500) but only 3 rows exist.
        assert len(_json(resp)["data"]["messages"]) == 3
