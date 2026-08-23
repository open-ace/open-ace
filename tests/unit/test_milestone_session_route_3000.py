"""#3000: milestone session viewer — full-transcript fallback + message tagging.

The viewer used to render only the status badge when the tracking session's
mapped CLI session had no ``agent_sessions`` row (this deployment persists the
transcript under the tracking id) and the milestone-filtered fallback found no
tagged messages (verification-line messages are 100% untagged). These tests
pin the new behavior: the fallback returns the tracking session's FULL
transcript, AgentSession payloads are normalized via ``to_dict()`` (ISO
timestamps + milestone_id), and ``tag_untagged_messages`` back-fills
attribution.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.modules.workspace.session_manager import AgentSession, SessionManager, SessionMessage

pytestmark = [pytest.mark.issue(3000)]


# ── Route tests ─────────────────────────────────────────────────────────────


@pytest.fixture
def auto_db(tmp_path):
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_3000.db")
            from app.repositories.schema_init import load_schema_from_file

            load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO users (username, email, password_hash, role)"
                    " VALUES ('admin', 'admin@test.com', 'hash', 'platform_admin')"
                )
                conn.commit()
            finally:
                conn.close()
            yield db_path
        finally:
            db_mod.adapt_sql = orig


@pytest.fixture
def client(auto_db, monkeypatch):
    from app import create_app
    from app.repositories.database import Database
    from app.repositories.user_repo import UserRepository

    database = Database(db_url=f"sqlite:///{auto_db}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{auto_db}")
    app = create_app({"TESTING": True})
    monkeypatch.setattr("app.routes.autonomous.user_repo", UserRepository(db=database))
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


def _mock_auth():
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": 1,
            "username": "admin",
            "email": "admin@test.com",
            "role": "platform_admin",
            "tenant_id": None,
        },
    )


def _repo():
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
    repo.get_milestone.return_value = {
        "milestone_id": "ms-1",
        "workflow_id": "wf-1",
        "session_id": "track-1",
    }
    return repo


def _tracking_row(cli_session_id: str):
    row = MagicMock()
    row.cli_session_id = cli_session_id
    row.get = MagicMock(side_effect=lambda k, d=None: {"cli_session_id": cli_session_id}.get(k, d))
    return row


def test_cli_row_missing_falls_back_to_full_tracking_transcript(client):
    """Mapped CLI session has no row → serve the tracking session unfiltered."""
    repo = _repo()
    full_transcript = {
        "session_id": "track-1",
        "status": "completed",
        "messages": [{"role": "assistant", "content": "all 128 messages"}],
    }
    sm = MagicMock()
    sm.get_session.side_effect = [
        _tracking_row("actual-1"),  # resolve: tracking row maps to actual-1
        None,  # actual-1 has no agent_sessions row
        full_transcript,  # fallback: full tracking transcript, unfiltered
    ]

    with _mock_auth():
        with patch("app.routes.autonomous.auto_repo", repo):
            with patch(
                "app.modules.workspace.session_manager.SessionManager",
                return_value=sm,
            ):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session"] == full_transcript
    # The load-bearing property: the LAST call has no message_milestone_id.
    assert sm.get_session.call_args_list == [
        (("track-1",), {}),
        (("actual-1",), {"include_messages": True}),
        (("track-1",), {"include_messages": True}),
    ]


def test_no_cli_mapping_serves_full_transcript_in_one_call(client):
    repo = _repo()
    full_transcript = {"session_id": "track-1", "status": "completed", "messages": []}
    sm = MagicMock()
    sm.get_session.side_effect = [_tracking_row(""), full_transcript]

    with _mock_auth():
        with patch("app.routes.autonomous.auto_repo", repo):
            with patch(
                "app.modules.workspace.session_manager.SessionManager",
                return_value=sm,
            ):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

    assert resp.status_code == 200
    assert resp.get_json()["session"] == full_transcript
    assert sm.get_session.call_args_list == [
        (("track-1",), {}),
        (("track-1",), {"include_messages": True}),
    ]


def test_cli_row_present_returns_actual_transcript(client):
    repo = _repo()
    actual_transcript = {
        "session_id": "actual-1",
        "status": "completed",
        "messages": [{"role": "assistant", "content": "Full transcript"}],
    }
    sm = MagicMock()
    sm.get_session.side_effect = [_tracking_row("actual-1"), actual_transcript]

    with _mock_auth():
        with patch("app.routes.autonomous.auto_repo", repo):
            with patch(
                "app.modules.workspace.session_manager.SessionManager",
                return_value=sm,
            ):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

    assert resp.status_code == 200
    assert resp.get_json()["session"] == actual_transcript
    assert sm.get_session.call_count == 2


def test_agent_session_payload_is_normalized_with_iso_timestamps(client):
    """AgentSession objects pass through to_dict(): ISO timestamps + milestone_id."""
    repo = _repo()
    message = SessionMessage(
        session_id="actual-1",
        role="assistant",
        content="Done",
        timestamp=datetime(2026, 8, 22, 12, 34, 56),
        milestone_id="ms-9",
    )
    actual = AgentSession(
        session_id="actual-1",
        tool_name="claude",
        status="completed",
        messages=[message],
    )
    sm = MagicMock()
    sm.get_session.side_effect = [_tracking_row("actual-1"), actual]

    with _mock_auth():
        with patch("app.routes.autonomous.auto_repo", repo):
            with patch(
                "app.modules.workspace.session_manager.SessionManager",
                return_value=sm,
            ):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

    assert resp.status_code == 200
    session = resp.get_json()["session"]
    assert session["session_id"] == "actual-1"
    assert session["messages"][0]["milestone_id"] == "ms-9"
    # ISO format — the property the asdict path (http-date datetimes) broke.
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", session["messages"][0]["timestamp"])


# ── tag_untagged_messages tests ─────────────────────────────────────────────


@pytest.fixture
def tag_db(tmp_path):
    from app.repositories.schema_init import load_schema_from_file

    db_path = str(tmp_path / "tag.db")
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_sessions (session_id, tool_name, tenant_id, status)"
            " VALUES ('sess-1', 'claude', 1, 'completed')"
        )
        conn.execute(
            "INSERT INTO session_messages (session_id, role, content, tenant_id, milestone_id)"
            " VALUES ('sess-1', 'user', 'u1', 1, '')"
        )
        conn.execute(
            "INSERT INTO session_messages (session_id, role, content, tenant_id, milestone_id)"
            " VALUES ('sess-1', 'assistant', 'a1', 1, '')"
        )
        conn.execute(
            "INSERT INTO session_messages (session_id, role, content, tenant_id, milestone_id)"
            " VALUES ('sess-1', 'tool', 't1', 1, 'ms-older')"
        )
        conn.commit()
    finally:
        conn.close()
    yield db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_tag_untagged_messages_tags_only_empty_rows(tag_db):
    retagged = SessionManager(db_path=tag_db).tag_untagged_messages("sess-1", "ms-new")

    assert retagged == 2
    conn = sqlite3.connect(tag_db)
    try:
        rows = dict(
            conn.execute(
                "SELECT milestone_id, COUNT(*) FROM session_messages GROUP BY milestone_id"
            )
        )
    finally:
        conn.close()
    assert rows == {"ms-new": 2, "ms-older": 1}


def test_tag_untagged_messages_missing_session_returns_zero(tag_db):
    assert SessionManager(db_path=tag_db).tag_untagged_messages("nope", "ms-new") == 0


def test_tag_untagged_messages_retag_is_noop(tag_db):
    manager = SessionManager(db_path=tag_db)
    assert manager.tag_untagged_messages("sess-1", "ms-new") == 2
    # Everything is tagged now — a second sweep must not move anything.
    assert manager.tag_untagged_messages("sess-1", "ms-other") == 0


def test_tag_untagged_messages_rejects_empty_args(tag_db):
    manager = SessionManager(db_path=tag_db)
    assert manager.tag_untagged_messages("", "ms") == 0
    assert manager.tag_untagged_messages("sess-1", "") == 0


def test_tag_untagged_messages_fails_closed_on_unresolved_tenant(tag_db):
    """A session row with no resolvable tenant must not retag anything."""
    conn = sqlite3.connect(tag_db)
    try:
        # tenant_id is NOT NULL in the schema; 0 is a non-positive value that
        # _normalize_tenant_id maps to None → the write must fail closed.
        conn.execute(
            "INSERT INTO agent_sessions (session_id, tool_name, tenant_id, status)"
            " VALUES ('sess-null-tenant', 'claude', 0, 'completed')"
        )
        conn.execute(
            "INSERT INTO session_messages (session_id, role, content, tenant_id, milestone_id)"
            " VALUES ('sess-null-tenant', 'user', 'u', 1, '')"
        )
        conn.commit()
    finally:
        conn.close()

    assert SessionManager(db_path=tag_db).tag_untagged_messages("sess-null-tenant", "ms") == 0

    conn = sqlite3.connect(tag_db)
    try:
        still_untagged = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = 'sess-null-tenant'"
            " AND milestone_id = ''"
        ).fetchone()[0]
    finally:
        conn.close()
    assert still_untagged == 1
