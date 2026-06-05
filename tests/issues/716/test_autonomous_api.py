"""Integration tests for autonomous API endpoints using Flask test client."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_api.db")
            db = Database(db_url=f"sqlite:///{db_path}")
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT DEFAULT 'user',
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "admin"),
                )
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("testuser", "test@test.com", "hash456", "user"),
                )
                conn.commit()

                from app.modules.workspace.autonomous import get_ddl_statements

                for sql in get_ddl_statements():
                    cursor.execute(sql)
                conn.commit()
            finally:
                conn.close()
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


def _mock_auth(user_id=1, role="admin"):
    """Patch auth to bypass authentication."""
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": user_id,
            "username": "admin" if role == "admin" else "testuser",
            "email": f"{role}@test.com",
            "role": role,
        },
    )


@pytest.fixture
def client(auto_db):
    """Create a Flask test client with session cookie set."""
    from app import create_app

    app = create_app({"TESTING": True})
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


def _make_repo():
    """Create a mock AutonomousWorkflowRepository."""
    repo = MagicMock()
    repo.create_workflow.return_value = {
        "workflow_id": "wf-mock",
        "title": "Mock",
        "status": "pending",
        "cli_tool": "claude-code",
    }
    repo.get_workflow.return_value = None
    repo.list_workflows.return_value = []
    repo.list_milestones.return_value = []
    repo.create_milestone.return_value = {"milestone_id": "ms-mock", "workflow_id": "wf-mock"}
    repo.create_event.return_value = {"id": 1}
    repo.update_workflow.return_value = {}
    repo.delete_workflow.return_value = None
    repo.get_milestone.return_value = None
    repo.cancel_milestones_after.return_value = []
    return repo


# ── Workflow CRUD Tests ──────────────────────────────────────────────────


class TestCreateWorkflow:
    """Tests for POST /api/autonomous/workflows."""

    def test_create_success(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "title": "Test Task",
                        "requirements_text": "Build a feature",
                        "cli_tool": "claude-code",
                        "model": "claude-sonnet-4-6",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True

    def test_create_missing_requirements(self, client):
        with _mock_auth():
            resp = client.post(
                "/api/autonomous/workflows",
                json={"cli_tool": "claude-code"},
            )
        assert resp.status_code == 400

    def test_create_missing_cli_tool(self, client):
        with _mock_auth():
            resp = client.post(
                "/api/autonomous/workflows",
                json={"requirements_text": "Build something"},
            )
        assert resp.status_code == 400

    def test_create_missing_project_path(self, client):
        with _mock_auth():
            resp = client.post(
                "/api/autonomous/workflows",
                json={"requirements_text": "Build something", "cli_tool": "claude-code"},
            )
        assert resp.status_code == 400

    def test_create_with_issue_url(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_issue_url": "https://github.com/user/repo/issues/42",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 201

    def test_create_new_project_no_path_needed(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "New project",
                        "cli_tool": "claude-code",
                        "is_new_project": True,
                    },
                )
        assert resp.status_code == 201


class TestListWorkflows:
    """Tests for GET /api/autonomous/workflows."""

    def test_list_success(self, client):
        repo = _make_repo()
        repo.list_workflows.return_value = [
            {"workflow_id": "wf-1", "title": "Task 1"},
            {"workflow_id": "wf-2", "title": "Task 2"},
        ]
        with _mock_auth(role="admin"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["workflows"]) == 2

    def test_list_regular_user_filters_own(self, client):
        """Non-admin users should only see their own workflows."""
        repo = _make_repo()
        with _mock_auth(user_id=2, role="user"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows")
                assert resp.status_code == 200
                # Check that user_id filter was applied
                call_kwargs = repo.list_workflows.call_args
                assert call_kwargs[1]["user_id"] == 2

    def test_list_admin_sees_all(self, client):
        repo = _make_repo()
        with _mock_auth(user_id=1, role="admin"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows")
                assert resp.status_code == 200
                call_kwargs = repo.list_workflows.call_args
                assert call_kwargs[1]["user_id"] is None


class TestGetWorkflow:
    """Tests for GET /api/autonomous/workflows/<id>."""

    def test_get_success(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "title": "Test",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 200
        assert resp.get_json()["workflow"]["workflow_id"] == "wf-1"

    def test_get_not_found(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/nonexistent")
        assert resp.status_code == 404

    def test_get_other_users_workflow_forbidden(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
        }
        with _mock_auth(user_id=2, role="user"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 403


class TestDeleteWorkflow:
    """Tests for DELETE /api/autonomous/workflows/<id>."""

    def test_delete_success(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.delete("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 200
        repo.delete_workflow.assert_called_once_with("wf-1")

    def test_delete_not_found(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.delete("/api/autonomous/workflows/nonexistent")
        assert resp.status_code == 404


# ── Workflow Control Tests ───────────────────────────────────────────────


class TestPauseWorkflow:
    """Tests for POST /api/autonomous/workflows/<id>/pause."""

    def test_pause_success(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "planning",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/pause")
        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["status"] == "paused"

    def test_pause_already_paused(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "paused",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/pause")
        assert resp.status_code == 400


class TestResumeWorkflow:
    """Tests for POST /api/autonomous/workflows/<id>/resume."""

    def test_resume_success(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "paused",
            "current_phase": "planning",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/resume")
        assert resp.status_code == 200

    def test_resume_not_paused(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "planning",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/resume")
        assert resp.status_code == 400


class TestStopWorkflow:
    """Tests for POST /api/autonomous/workflows/<id>/stop."""

    def test_stop_success(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "planning",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/stop")
        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["status"] == "cancelled"


class TestMarkDone:
    """Tests for POST /api/autonomous/workflows/<id>/done."""

    def test_mark_done(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "waiting",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-1/done",
                    json={"selected_branch": "feature/v2"},
                )
        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["current_phase"] == "merge"
        assert call_args[1]["branch_name"] == "feature/v2"


# ── Milestone Tests ──────────────────────────────────────────────────────


class TestGetTimeline:
    """Tests for GET /api/autonomous/workflows/<id>/timeline."""

    def test_get_timeline(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.list_milestones.return_value = [
            {"milestone_id": "ms-1", "phase": "planning", "status": "completed"},
            {"milestone_id": "ms-2", "phase": "development", "status": "in_progress"},
        ]
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/timeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["milestones"]) == 2


class TestCancelMilestone:
    """Tests for POST /api/autonomous/workflows/<id>/milestones/<mid>/cancel."""

    def test_cancel_milestone(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": "wf-1",
        }
        repo.cancel_milestones_after.return_value = [
            {"milestone_id": "ms-2", "status": "cancelled"},
        ]
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/milestones/ms-1/cancel")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cancelled"] == 1


class TestForkMilestone:
    """Tests for POST /api/autonomous/workflows/<id>/milestones/<mid>/fork."""

    def test_fork_milestone(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": "wf-1",
            "phase": "development",
            "dev_round": 1,
            "title": "Dev round 1",
        }
        repo.create_milestone.return_value = {
            "milestone_id": "ms-fork",
            "title": "Forked",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-1/milestones/ms-1/fork",
                    json={"branch_name": "fork/from-ms-1"},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["fork_milestone"]["milestone_id"] == "ms-fork"


# ── Auxiliary Tests ──────────────────────────────────────────────────────


class TestGetTools:
    """Tests for GET /api/autonomous/tools."""

    def test_get_tools(self, client):
        with _mock_auth():
            resp = client.get("/api/autonomous/tools")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["tools"]) >= 4
        tool_ids = [t["id"] for t in data["tools"]]
        assert "claude-code" in tool_ids
        assert "codex" in tool_ids
        assert "qwen-code-cli" in tool_ids
        assert "openclaw" in tool_ids


class TestGetModels:
    """Tests for GET /api/autonomous/models."""

    def test_get_models_with_tenant(self, client):
        mock_proxy = MagicMock()
        mock_proxy.get_tool_model_pool.return_value = [
            {"name": "claude-sonnet-4-6"},
            {"name": "claude-opus-4-8"},
        ]
        auth = _mock_auth()
        with auth:
            with patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
            ):
                # Need to also set g.tenant_id before the route runs
                with patch(
                    "app.routes.autonomous.getattr",
                    side_effect=lambda obj, name, default: (
                        1 if name == "tenant_id" else getattr(obj, name, default)
                    ),
                ):
                    resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["models"]) == 2

    def test_get_models_no_tenant_returns_empty(self, client):
        with _mock_auth():
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["models"] == []


# ── Auth Tests ───────────────────────────────────────────────────────────


class TestAuthRequired:
    """Tests that endpoints require authentication."""

    def test_list_requires_auth(self, client):
        resp = client.get("/api/autonomous/workflows")
        assert resp.status_code == 401

    def test_create_requires_auth(self, client):
        resp = client.post(
            "/api/autonomous/workflows",
            json={"requirements_text": "test", "cli_tool": "cc", "project_path": "/tmp"},
        )
        assert resp.status_code == 401

    def test_get_requires_auth(self, client):
        resp = client.get("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 401

    def test_tools_requires_auth(self, client):
        resp = client.get("/api/autonomous/tools")
        assert resp.status_code == 401
