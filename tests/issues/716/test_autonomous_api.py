"""Integration tests for autonomous API endpoints using Flask test client."""

import json
import os
import queue
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

    def test_get_models_with_tenant(self, client):
        """Models endpoint returns models when tenant_id is set on g."""
        mock_proxy = MagicMock()
        mock_proxy.get_tool_model_pool.return_value = [
            {"name": "claude-sonnet-4-6"},
            {"name": "claude-opus-4-8"},
        ]
        # Set g.tenant_id via a before_request hook on the test app
        from flask import g as flask_g

        def _set_tenant():
            flask_g.tenant_id = 1

        # Access the app from the client and register the hook
        client.application.before_request(_set_tenant)

        with _mock_auth():
            with patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
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
        mock_sm.get_session.assert_called_once_with("sess-123", include_messages=True)

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
