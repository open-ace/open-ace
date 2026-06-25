"""Integration tests for autonomous API endpoints using Flask test client."""

import json
import os
import queue
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.modules.workspace.api_key_proxy import APIKeyProxyService
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
                    try:
                        cursor.execute(sql)
                    except Exception:
                        pass
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
    repo.count_workflows.return_value = 0
    repo.list_milestones.return_value = []
    repo.create_milestone.return_value = {"milestone_id": "ms-mock", "workflow_id": "wf-mock"}
    repo.create_event.return_value = {"id": 1}
    repo.update_workflow.return_value = {}
    repo.delete_workflow.return_value = None
    repo.get_milestone.return_value = None
    repo.cancel_milestones_after.return_value = []
    return repo


# ── Workflow CRUD Tests ──────────────────────────────────────────────────


class TestIssueSelectorParser:
    """Tests for mixed GitHub issue selector parsing."""

    def test_parse_mixed_tokens(self):
        from app.routes.autonomous import _parse_issue_selectors

        selectors, ignored = _parse_issue_selectors(
            "12 14-15 https://github.com/open-ace/open-ace/issues/20 15"
        )

        assert [item["issue_number"] for item in selectors] == [12, 14, 15, 20]
        assert ignored == []
        assert selectors[-1]["requirements_issue_url"].endswith("/issues/20")

    def test_parse_invalid_tokens_are_ignored(self):
        from app.routes.autonomous import _parse_issue_selectors

        selectors, ignored = _parse_issue_selectors("abc 7-3 0 -1")

        assert selectors == []
        assert ignored == ["abc", "7-3", "0", "-1"]


class TestCreateWorkflow:
    """Tests for POST /api/autonomous/workflows."""

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """These tests exercise creation logic, not the quota gate. Stub the
        QuotaManager to allow-by-default so the (real, DB-backed) quota check
        doesn't reach the environment's default DB and spuriously 429."""
        mock = MagicMock()
        mock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with patch("app.modules.governance.quota_manager.QuotaManager", mock):
            yield

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
        payload = repo.create_workflow.call_args[0][0]
        snapshot = json.loads(payload["definition_snapshot"])
        assert snapshot["requirements_mode"] == "text"
        assert snapshot["requirements_text"] == "Build a feature"
        assert snapshot["cli_tool"] == "claude-code"

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

    def test_create_with_multiple_issue_selectors(self, client):
        repo = _make_repo()
        repo.create_workflow.side_effect = [
            {
                "workflow_id": "wf-1",
                "title": "Batch Task (#12)",
                "status": "pending",
                "github_issue_number": 12,
                "batch_id": "batch-1",
                "batch_order": 1,
                "batch_total": 3,
            },
            {
                "workflow_id": "wf-2",
                "title": "Batch Task (#14)",
                "status": "queued",
                "github_issue_number": 14,
                "batch_id": "batch-1",
                "batch_order": 2,
                "batch_total": 3,
            },
            {
                "workflow_id": "wf-3",
                "title": "Batch Task (#15)",
                "status": "queued",
                "github_issue_number": 15,
                "batch_id": "batch-1",
                "batch_order": 3,
                "batch_total": 3,
            },
        ]
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "title": "Batch Task",
                        "requirements_issue_input": "12 14-15 bad-token",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["workflow"]["workflow_id"] == "wf-1"
        assert [wf["workflow_id"] for wf in data["workflows"]] == ["wf-1", "wf-2", "wf-3"]
        assert data["ignored_issue_tokens"] == ["bad-token"]

        call_payloads = [call.args[0] for call in repo.create_workflow.call_args_list]
        assert [payload["github_issue_number"] for payload in call_payloads] == [12, 14, 15]
        assert [payload["status"] for payload in call_payloads] == ["pending", "queued", "queued"]
        snapshots = [json.loads(payload["definition_snapshot"]) for payload in call_payloads]
        assert all(
            snapshot["requirements_issue_input_raw"] == "12 14-15 bad-token"
            for snapshot in snapshots
        )
        assert [snapshot["resolved_issue_number"] for snapshot in snapshots] == [12, 14, 15]
        assert [snapshot["batch_order"] for snapshot in snapshots] == [1, 2, 3]

    def test_create_with_only_invalid_issue_selectors(self, client):
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_issue_input": "abc 9-3 nope",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 400

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
        repo.count_workflows.return_value = 2
        with _mock_auth(role="admin"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["workflows"]) == 2
        assert data["total"] == 2
        assert data["limit"] == 50
        assert data["offset"] == 0

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

    def test_list_passes_search_limit_offset_and_status(self, client):
        repo = _make_repo()
        repo.count_workflows.return_value = 7
        with _mock_auth(role="admin"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get(
                    "/api/autonomous/workflows?status=queued&search=issue%2012&limit=25&offset=50"
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 7
        assert data["limit"] == 25
        assert data["offset"] == 50
        call_kwargs = repo.list_workflows.call_args[1]
        assert call_kwargs["status"] == "queued"
        assert call_kwargs["search"] == "issue 12"
        assert call_kwargs["limit"] == 25
        assert call_kwargs["offset"] == 50


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

    def test_get_parses_definition_snapshot(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "title": "Test",
            "definition_snapshot": json.dumps({"requirements_mode": "text"}),
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1")
        assert resp.status_code == 200
        snapshot = resp.get_json()["workflow"]["definition_snapshot"]
        assert snapshot["requirements_mode"] == "text"

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


class TestDeleteBatch:
    """Tests for DELETE /api/autonomous/batches/<id>."""

    def test_delete_batch_success(self, client):
        repo = _make_repo()
        repo.list_batch_workflows.return_value = [
            {"workflow_id": "wf-1", "user_id": 1, "batch_id": "batch-1"},
            {"workflow_id": "wf-2", "user_id": 1, "batch_id": "batch-1"},
        ]
        repo.delete_batch.return_value = 2
        with _mock_auth(user_id=1, role="user"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.delete("/api/autonomous/batches/batch-1")
        assert resp.status_code == 200
        assert resp.get_json()["deleted_count"] == 2
        repo.delete_batch.assert_called_once_with("batch-1")

    def test_delete_batch_not_found(self, client):
        repo = _make_repo()
        repo.list_batch_workflows.return_value = []
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.delete("/api/autonomous/batches/missing-batch")
        assert resp.status_code == 404

    def test_delete_batch_forbidden_for_other_users(self, client):
        repo = _make_repo()
        repo.list_batch_workflows.return_value = [
            {"workflow_id": "wf-1", "user_id": 2, "batch_id": "batch-1"},
        ]
        with _mock_auth(user_id=1, role="user"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.delete("/api/autonomous/batches/batch-1")
        assert resp.status_code == 403


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

    def test_stop_cancels_queued_batch_siblings(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "planning",
            "batch_id": "batch-1",
        }
        repo.cancel_queued_batch_workflows.return_value = 1
        repo.list_batch_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "cancelled"},
            {"workflow_id": "wf-2", "status": "cancelled"},
        ]
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-1/stop")
        assert resp.status_code == 200
        repo.cancel_queued_batch_workflows.assert_called_once_with("batch-1", "wf-1")


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
        repo.get_milestone_usage_summary.return_value = {
            "ms-1": {
                "llm_session_id": "sess-plan",
                "llm_total_tokens": 1234,
                "llm_request_count": 7,
            }
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/timeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["milestones"]) == 2
        assert data["milestones"][0]["llm_session_id"] == "sess-plan"
        assert data["milestones"][0]["llm_total_tokens"] == 1234
        assert data["milestones"][0]["llm_request_count"] == 7
        assert data["milestones"][1]["llm_total_tokens"] == 0

    def test_get_timeline_backfills_diff_stats_per_milestone(self, client):
        repo = _make_repo()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "worktree_path": "/tmp/test-worktree",
            "project_path": "/tmp/test-project",
        }
        repo.list_milestones.return_value = [
            {
                "milestone_id": "ms-dev",
                "phase": "development",
                "status": "completed",
                "commit_shas": json.dumps(["aaa111", "bbb222"]),
                "diff_stats": "",
            },
            {
                "milestone_id": "ms-pr-fix",
                "phase": "pr_review",
                "status": "completed",
                "commit_shas": json.dumps(["ccc333"]),
                "diff_stats": "",
            },
        ]
        repo.get_milestone_usage_summary.return_value = {}

        gh = MagicMock()
        gh.get_commit_diff_stats.side_effect = [
            {"additions": 100, "deletions": 20, "files": 2, "commits": 1},
            {"additions": 30, "deletions": 5, "files": 1, "commits": 1},
            {"additions": 8, "deletions": 3, "files": 2, "commits": 1},
        ]

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch(
                    "app.modules.workspace.autonomous.github_ops.GitHubOps",
                    return_value=gh,
                ):
                    resp = client.get("/api/autonomous/workflows/wf-1/timeline")

        assert resp.status_code == 200
        data = resp.get_json()

        ms_dev_stats = json.loads(data["milestones"][0]["diff_stats"])
        assert ms_dev_stats == {
            "additions": 130,
            "deletions": 25,
            "files": 3,
            "commits": 2,
        }

        ms_pr_fix_stats = json.loads(data["milestones"][1]["diff_stats"])
        assert ms_pr_fix_stats == {
            "additions": 8,
            "deletions": 3,
            "files": 2,
            "commits": 1,
        }
        repo.update_milestone.assert_any_call(
            "ms-dev",
            {
                "diff_stats": json.dumps(
                    {
                        "additions": 130,
                        "deletions": 25,
                        "files": 3,
                        "commits": 2,
                    }
                )
            },
        )
        repo.update_milestone.assert_any_call(
            "ms-pr-fix",
            {
                "diff_stats": json.dumps(
                    {
                        "additions": 8,
                        "deletions": 3,
                        "files": 2,
                        "commits": 1,
                    }
                )
            },
        )


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
                resp = client.post(
                    "/api/autonomous/workflows/wf-1/milestones/ms-1/cancel",
                    json={"user_feedback": "Please fix this issue"},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cancelled"] == 1


class TestForkMilestone:
    """Tests for POST /api/autonomous/workflows/<id>/milestones/<mid>/fork."""

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """These tests exercise fork logic, not the quota gate. Stub the
        QuotaManager to allow-by-default so the gate doesn't reach the
        environment's default DB and spuriously 429 the fork."""
        mock = MagicMock()
        mock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with patch("app.modules.governance.quota_manager.QuotaManager", mock):
            yield

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
                    json={
                        "user_feedback": "Try a different approach",
                        "branch_name": "fork/from-ms-1",
                        "pause_original": False,
                    },
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "fork_workflow" in data


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

    def test_get_models_returns_normalized_models(self, client):
        """Endpoint extracts models and normalizes display names.

        The route now uses the provider-agnostic ``get_tool_models`` (matched by
        cli_tools, not provider). Regression: previously it read ``g.tenant_id``
        (never set → always []) and returned the whole pool dict.
        """
        mock_proxy = MagicMock()
        mock_proxy.get_tool_models.return_value = {
            "models": [
                {"name": "claude-sonnet-4-6", "id": "claude-sonnet-4-6"},
                # entry with only an id -> name should fall back to id
                {"id": "claude-opus-4-8"},
            ],
            "empty_reason": None,
        }

        with _mock_auth():
            with patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
            ):
                resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        names = [m["name"] for m in data["models"]]
        assert names == ["claude-sonnet-4-6", "claude-opus-4-8"]
        # Local workspace resolves to the default single tenant (1) and local scope;
        # provider is NOT passed (provider-agnostic).
        mock_proxy.get_tool_models.assert_called_once_with(
            tenant_id=1, tool_name="claude-code", scope="local"
        )

    def test_get_models_no_keys_returns_empty(self, client):
        """No configured keys -> empty list (not because tenant is missing)."""
        mock_proxy = MagicMock()
        mock_proxy.get_tool_models.return_value = {
            "models": [],
            "empty_reason": "No active claude-code API keys configured for scope 'local'",
        }
        with _mock_auth():
            with patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
            ):
                resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["models"] == []
        assert "No active" in data["empty_reason"]

    def test_get_models_remote_derives_tenant_from_machine(self, client):
        """Remote workspace derives tenant_id from the machine record.

        ``scope`` must be 'remote' (matching keys tagged remote *or* shared);
        querying with 'shared' would silently miss every remote-only key.
        """
        mock_proxy = MagicMock()
        mock_proxy.get_tool_models.return_value = {"models": [], "empty_reason": None}
        mock_mgr = MagicMock()
        mock_mgr.check_user_access.return_value = "admin"
        mock_mgr.get_machine.return_value = {"tenant_id": 7}
        with _mock_auth():
            with (
                patch(
                    "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                    return_value=mock_proxy,
                ),
                patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_mgr,
                ),
            ):
                resp = client.get(
                    "/api/autonomous/models?tool=claude-code"
                    "&workspace_type=remote&machine_id=m-42"
                )
        assert resp.status_code == 200
        mock_mgr.check_user_access.assert_called_once()
        mock_mgr.get_machine.assert_called_once_with("m-42")
        mock_proxy.get_tool_models.assert_called_once_with(
            tenant_id=7, tool_name="claude-code", scope="remote"
        )

    def test_get_models_remote_denies_without_access(self, client):
        """A user without access to the machine gets 404, never the model list."""
        mock_proxy = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.check_user_access.return_value = None  # no access
        with _mock_auth():
            with (
                patch(
                    "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                    return_value=mock_proxy,
                ),
                patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_mgr,
                ),
            ):
                resp = client.get(
                    "/api/autonomous/models?tool=claude-code"
                    "&workspace_type=remote&machine_id=m-42"
                )
        assert resp.status_code == 404
        mock_proxy.get_tool_models.assert_not_called()

    def test_get_models_exception_returns_empty(self, client):
        """Any unexpected error in the model query is swallowed.

        Only the query/formatting is wrapped — a failure there reasonably
        degrades to an empty list (the dropdown shows "Default").
        """
        mock_proxy = MagicMock()
        mock_proxy.get_tool_models.side_effect = RuntimeError("boom")
        with _mock_auth():
            with patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
            ):
                resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["models"] == []

    def test_get_models_remote_lookup_failure_not_masked_as_success(self, client):
        """Remote machine-lookup failure must not be masked as success+empty.

        The remote resolution (get_remote_agent_manager / check_user_access /
        get_machine) runs outside the try that swallows query errors, so an
        infrastructure failure there surfaces as an error rather than a
        misleading {"success": True, "models": []}. Regression guard for the
        behavior change flagged in review.
        """
        mock_proxy = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.check_user_access.side_effect = RuntimeError("db down")
        with _mock_auth():
            with (
                patch(
                    "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                    return_value=mock_proxy,
                ),
                patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_mgr,
                ),
            ):
                resp = client.get(
                    "/api/autonomous/models?tool=claude-code"
                    "&workspace_type=remote&machine_id=m-42"
                )
        # Must not be a misleading "success, no models" — surface the failure.
        assert resp.status_code != 200
        mock_proxy.get_tool_models.assert_not_called()


class TestGetToolModelsExtractor:
    """Unit tests for APIKeyProxyService.get_tool_models (provider-agnostic).

    The extractor branches on the canonical tool name because each tool stores
    its models in a different location (see APIKeyManagement.tsx templates and
    remote-agent cli_settings.py). These tests stub _collect_tool_key_settings
    (which returns the per-key settings list) so no DB is needed.
    """

    def _proxy(self):
        # Bypass __init__ — it needs an encryption key/DB only for key
        # management, which get_tool_models (pure extraction over the stubbed
        # per-key settings) does not touch.
        return object.__new__(APIKeyProxyService)

    @staticmethod
    def _keys(*settings):
        """Wrap per-key settings dicts as ranked entries (rank irrelevant here)."""
        return [((-priority, -100, i), s) for i, (priority, s) in enumerate(settings)]

    def test_no_keys_returns_empty_with_reason(self):
        proxy = self._proxy()
        with patch.object(proxy, "_collect_tool_key_settings", return_value=[]):
            result = proxy.get_tool_models(1, "claude-code", "local")
        assert result["models"] == []
        assert "No active" in result["empty_reason"]

    def test_claude_extracts_from_env(self):
        """claude-code stores models in env vars, not modelProviders."""
        proxy = self._proxy()
        settings = {
            "env": {
                "ANTHROPIC_MODEL": "glm-5",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5",  # dup of ANTHROPIC_MODEL
            }
        }
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys((1, settings))
        ):
            result = proxy.get_tool_models(1, "claude-code", "local")
        ids = [m["id"] for m in result["models"]]
        assert ids == ["glm-5", "glm-5.1"]  # deduped, order preserved
        assert all(m["name"] == m["id"] for m in result["models"])

    def test_zcode_extracts_from_model_field(self):
        """zcode stores models under a top-level `model` dict; strip provider/."""
        proxy = self._proxy()
        settings = {"model": {"main": "zai/glm-5.2", "lite": "zai/glm-4.5-air"}}
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys((1, settings))
        ):
            result = proxy.get_tool_models(1, "zcode", "remote")
        names = sorted(m["name"] for m in result["models"])
        assert names == ["glm-4.5-air", "glm-5.2"]  # prefix stripped

    def test_qwen_flattens_all_model_providers(self):
        """qwen/codex: union models across every modelProviders subkey."""
        proxy = self._proxy()
        settings = {
            "modelProviders": {
                "openai": [{"id": "glm-5", "name": "glm-5"}],
                "anthropic": [{"id": "claude-sonnet", "name": "Claude Sonnet"}],
            }
        }
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys((1, settings))
        ):
            result = proxy.get_tool_models(1, "qwen-code", "local")
        ids = sorted(m["id"] for m in result["models"])
        assert ids == ["claude-sonnet", "glm-5"]

    def test_keys_present_with_no_matching_models(self):
        """Keys exist but none configure models -> empty with reason."""
        proxy = self._proxy()
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys((1, {"env": {}}))
        ):
            result = proxy.get_tool_models(1, "claude-code", "local")
        assert result["models"] == []
        assert "do not configure" in result["empty_reason"]

    def test_multi_key_claude_unions_models_across_keys(self):
        """Multi-key claude-code: union env models from ALL keys, not just top.

        Regression: an earlier version delegated to get_cli_settings_for_tool,
        whose merge carries env from only the highest-priority key — so a
        tenant with two claude-code keys saw only the top key's models. The
        extractor now walks every key and unions.
        """
        proxy = self._proxy()
        # Two keys, each configuring a different model. Priority: key A (10) > B (5).
        key_a = (10, {"env": {"ANTHROPIC_MODEL": "glm-5"}})
        key_b = (5, {"env": {"ANTHROPIC_MODEL": "glm-5.1"}})
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys(key_a, key_b)
        ):
            result = proxy.get_tool_models(1, "claude-code", "local")
        assert sorted(m["id"] for m in result["models"]) == ["glm-5", "glm-5.1"]
        # Highest-priority key's model discovered first.
        assert result["models"][0]["id"] == "glm-5"

    def test_multi_key_zcode_unions_models_across_keys(self):
        """Multi-key zcode: union top-level model entries from all keys."""
        proxy = self._proxy()
        key_a = (10, {"model": {"main": "zai/glm-5.2"}})
        key_b = (5, {"model": {"main": "zai/glm-4.5-air"}})
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys(key_a, key_b)
        ):
            result = proxy.get_tool_models(1, "zcode", "remote")
        assert sorted(m["id"] for m in result["models"]) == ["glm-4.5-air", "glm-5.2"]

    def test_multi_key_qwen_unions_model_providers_across_keys(self):
        """Multi-key qwen: union modelProviders across keys (already correct)."""
        proxy = self._proxy()
        key_a = (10, {"modelProviders": {"openai": [{"id": "glm-5"}]}})
        key_b = (5, {"modelProviders": {"openai": [{"id": "glm-5.1"}]}})
        with patch.object(
            proxy, "_collect_tool_key_settings", return_value=self._keys(key_a, key_b)
        ):
            result = proxy.get_tool_models(1, "qwen-code", "local")
        assert sorted(m["id"] for m in result["models"]) == ["glm-5", "glm-5.1"]


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


# ── SSE Stream Tests ──────────────────────────────────────────────────────


class TestStreamWorkflowEvents:
    """Tests for GET /api/autonomous/workflows/<id>/events/stream (SSE)."""

    def test_stream_returns_event_stream(self, client):
        """SSE endpoint returns text/event-stream content type."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.routes.autonomous._get_event_emitter") as mock_emitter_fn:
                    mock_emitter = MagicMock()
                    mock_q = MagicMock()

                    # Simulate one event then GeneratorExit (client disconnects)
                    call_count = [0]

                    def mock_get(timeout=30):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            return {
                                "event_type": "status_change",
                                "data": {"status": "planning"},
                                "workflow_id": "wf-1",
                            }
                        raise queue.Empty

                    mock_q.get = mock_get
                    mock_q.get_timeout = mock_get
                    mock_emitter.subscribe.return_value = mock_q
                    mock_emitter.mark_read = MagicMock()
                    mock_emitter.unsubscribe = MagicMock()
                    mock_emitter_fn.return_value = mock_emitter

                    resp = client.get("/api/autonomous/workflows/wf-1/events/stream")

        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type
        assert resp.headers.get("Cache-Control") == "no-cache"

    def test_stream_workflow_not_found(self, client):
        """SSE endpoint returns 404 for nonexistent workflow."""
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/nonexistent/events/stream")
        assert resp.status_code == 404

    def test_stream_requires_ownership(self, client):
        """SSE endpoint returns 403 for other user's workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        with _mock_auth(user_id=2, role="user"):
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/events/stream")
        assert resp.status_code == 403


class TestGetMilestoneSession:
    """Tests for GET /api/autonomous/workflows/<id>/milestones/<mid>/session."""

    def test_get_session_success(self, client):
        """Returns session data for a milestone with a session_id."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": "wf-1",
            "session_id": "sess-123",
        }
        mock_session_data = {
            "session_id": "sess-123",
            "status": "completed",
            "messages": [{"role": "assistant", "content": "Done"}],
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls:
                    mock_sm = MagicMock()
                    mock_sm.get_session.return_value = mock_session_data
                    mock_sm_cls.return_value = mock_sm

                    resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["session"]["session_id"] == "sess-123"
        mock_sm.get_session.assert_called_once_with(
            "sess-123", include_messages=True, message_milestone_id="ms-1"
        )

    def test_get_session_no_session_id(self, client):
        """Returns null session when milestone has no session_id."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": "wf-1",
            "session_id": "",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["session"] is None

    def test_get_session_workflow_not_found(self, client):
        """Returns 404 when workflow doesn't exist."""
        repo = _make_repo()
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/nonexistent/milestones/ms-1/session")

        assert resp.status_code == 404

    def test_get_session_milestone_not_found(self, client):
        """Returns 404 when milestone doesn't exist or belongs to different workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = None
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-999/session")

        assert resp.status_code == 404

    def test_get_session_wrong_workflow(self, client):
        """Returns 404 when milestone belongs to a different workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = {"workflow_id": "wf-1", "user_id": 1}
        repo.get_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": "wf-OTHER",  # Different workflow
            "session_id": "sess-123",
        }
        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/session")

        assert resp.status_code == 404
