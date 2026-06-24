"""
Integration tests for the Run Timeline HTTP API blueprint.

Exercises the request layer that the unit tests do not: the feature-flag gate
(``{success: False, disabled: True}`` when off — the exact contract the React
component relies on to self-hide), authentication, session-access authorization,
and JSON serialization of the persisted run/event/approval models through the
read endpoints. The repository SQL is covered elsewhere, so the repo is mocked
here to keep these tests focused on the HTTP/authz/serialization layer.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    from flask import Flask

    from app.routes.run_timeline import run_timeline_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    # Mounted at the same prefix as in app/__init__.py.
    app.register_blueprint(run_timeline_bp, url_prefix="/api/remote")
    yield app


def _authenticated_client(app):
    """A test client that patches the route's auth helpers per request."""
    raw = app.test_client()
    user = {"id": 1, "role": "admin", "username": "test_admin"}

    class AuthenticatedClient:
        def get(self, *args, **kwargs):
            with (
                patch("app.routes.run_timeline._extract_token", return_value="tok"),
                patch("app.routes.run_timeline._load_user_from_token", return_value=user),
            ):
                return raw.get(*args, **kwargs)

    return AuthenticatedClient()


@pytest.fixture
def client(app):
    # Feature flag on + access allowed by default; individual tests override.
    with (
        patch("app.utils.config.is_run_timeline_enabled", return_value=True),
        patch(
            "app.routes.run_timeline._check_session_access",
            return_value=({"machine_id": "mac-1"}, None),
        ),
    ):
        yield _authenticated_client(app)


def _deny_access(session_id):
    """_check_session_access that denies (called within request/app context)."""
    from flask import jsonify

    return None, (jsonify({"error": "Access denied"}), 403)


# ── Real models so to_dict() serialization is exercised end-to-end ──────────


def _run_row(**over):
    base = {
        "run_id": "run-1",
        "session_id": "sess-1",
        "user_id": 1,
        "tenant_id": None,
        "machine_id": "mac-1",
        "tool_name": "claude-code",
        "provider": "anthropic",
        "cli_tool": "claude-code",
        "model": "sonnet",
        "status": "active",
        "started_at": None,
        "ended_at": None,
        "total_tokens": 180,
        "total_input_tokens": 150,
        "total_output_tokens": 30,
        "total_requests": 2,
        "metadata": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(over)
    return base


def _event_row(eid, **over):
    base = {
        "id": eid,
        "run_id": "run-1",
        "session_id": "sess-1",
        "event_type": "tool_use",
        "event_subtype": None,
        "role": None,
        "content": "ls -la",
        "tool_name": "Bash",
        "provider": None,
        "model": None,
        "key_id": None,
        "user_id": 1,
        "tenant_id": None,
        "machine_id": "mac-1",
        "metadata": None,
        "event_ts": None,
        "created_at": None,
    }
    base.update(over)
    return base


def _approval_row(**over):
    base = {
        "id": 1,
        "request_id": "req-1",
        "run_id": "run-1",
        "session_id": "sess-1",
        "tool_name": "Bash",
        "request_subtype": "execute",
        "request_details": None,
        "status": "approved",
        "decision": "allow",
        "decided_by": 1,
        "decided_by_name": "alice",
        "decision_metadata": None,
        "requested_at": None,
        "decided_at": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(over)
    return base


def _mock_repo(events=None, run=None, approvals=None, total=None):
    from app.modules.workspace.run_timeline.models import AgentApproval, AgentRun, RunEvent

    repo = MagicMock()
    repo.query_events.return_value = [RunEvent.from_row(r) for r in (events or [])]
    repo.count_events.return_value = total if total is not None else len(events or [])
    repo.get_run_by_session.return_value = AgentRun.from_row(run) if run else None
    repo.list_approvals.return_value = [AgentApproval.from_row(r) for r in (approvals or [])]
    return repo


# ── Tests ───────────────────────────────────────────────────────────────────


class TestFeatureFlagGate:
    def test_disabled_flag_returns_disabled_payload(self, app):
        # Flag off → blueprint short-circuits before auth; no token needed.
        with patch("app.utils.config.is_run_timeline_enabled", return_value=False):
            resp = app.test_client().get("/api/remote/sessions/sess-1/events")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is False
        assert data["disabled"] is True

    def test_disabled_flag_applies_to_approvals_endpoint_too(self, app):
        with patch("app.utils.config.is_run_timeline_enabled", return_value=False):
            resp = app.test_client().get("/api/remote/sessions/sess-1/approvals")

        assert resp.status_code == 200
        assert resp.get_json()["disabled"] is True


class TestAuthAndAccess:
    def test_unauthenticated_returns_401(self, app):
        with patch("app.utils.config.is_run_timeline_enabled", return_value=True):
            # No auth patches → no token → 401.
            resp = app.test_client().get("/api/remote/sessions/sess-1/events")
        assert resp.status_code == 401

    def test_access_denied_returns_403(self, app):
        with (
            patch("app.utils.config.is_run_timeline_enabled", return_value=True),
            patch("app.routes.run_timeline._check_session_access", side_effect=_deny_access),
        ):
            resp = _authenticated_client(app).get("/api/remote/sessions/sess-1/events")
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Access denied"


class TestEventsEndpoint:
    def test_returns_run_events_and_summary(self, client):
        events = [_event_row(1, event_type="session_created"), _event_row(2, event_type="tool_use")]
        with patch(
            "app.repositories.run_timeline_repo.RunTimelineRepository",
            return_value=_mock_repo(events=events, run=_run_row()),
        ):
            resp = client.get("/api/remote/sessions/sess-1/events")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Run summary serialized.
        assert data["run"]["model"] == "sonnet"
        assert data["run"]["total_tokens"] == 180
        # Events serialized in order.
        assert [e["event_type"] for e in data["events"]] == ["session_created", "tool_use"]
        assert data["total"] == 2
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_limit_and_order_query_params_forwarded_to_repo(self, client):
        repo = _mock_repo()
        with patch("app.repositories.run_timeline_repo.RunTimelineRepository", return_value=repo):
            client.get("/api/remote/sessions/sess-1/events?limit=5&offset=10&order=desc&after=7")

        repo.query_events.assert_called_once()
        _, kwargs = repo.query_events.call_args
        assert kwargs["limit"] == 5
        assert kwargs["offset"] == 10
        assert kwargs["order"] == "desc"
        assert kwargs["after_id"] == 7

    def test_limit_capped_at_max(self, client):
        repo = _mock_repo()
        with patch("app.repositories.run_timeline_repo.RunTimelineRepository", return_value=repo):
            client.get("/api/remote/sessions/sess-1/events?limit=5000")
        _, kwargs = repo.query_events.call_args
        assert kwargs["limit"] == 1000  # _MAX_LIMIT

    def test_no_run_yields_null(self, client):
        with patch(
            "app.repositories.run_timeline_repo.RunTimelineRepository",
            return_value=_mock_repo(events=[], run=None, total=0),
        ):
            resp = client.get("/api/remote/sessions/sess-1/events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["run"] is None
        assert data["events"] == []
        assert data["total"] == 0


class TestApprovalsEndpoint:
    def test_returns_approvals(self, client):
        approvals = [_approval_row(request_id="req-1"), _approval_row(request_id="req-2")]
        with patch(
            "app.repositories.run_timeline_repo.RunTimelineRepository",
            return_value=_mock_repo(approvals=approvals),
        ):
            resp = client.get("/api/remote/sessions/sess-1/approvals")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert [a["request_id"] for a in data["approvals"]] == ["req-1", "req-2"]
        assert data["approvals"][0]["status"] == "approved"
        assert data["total"] == 2
