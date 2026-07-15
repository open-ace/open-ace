"""Tests for GET /api/autonomous/workflows/<id>/pr-stats."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1",
        "user_id": 1,
        "title": "Test",
        "status": "completed",
        "requirements_text": "",
        "requirements_issue_url": "",
        "project_path": "/tmp/project",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "main",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "completed",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 2,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:00:00",
        "paused_at": None,
    }
    base.update(overrides)
    return base


def _make_client():
    import app.repositories.database as db_mod
    from app import create_app

    db_path = tempfile.mktemp(suffix=".db")
    db_url = f"sqlite:///{db_path}"
    orig = db_mod.adapt_sql
    orig_get_database_url = db_mod.get_database_url
    db_mod.adapt_sql = lambda sql: sql
    db_mod.get_database_url = lambda: db_url
    app = create_app({"TESTING": True})
    db = db_mod.Database(db_url=db_url)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (1, "admin", "admin@test.com", "hash123", "admin"),
        )
        conn.commit()

    client = app.test_client()
    client.set_cookie("session_token", "test-token")
    return client, db_path, orig, orig_get_database_url, db_mod


def _mock_auth(user_id=1, role="admin"):
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": user_id,
            "username": "admin" if role == "admin" else "testuser",
            "email": f"{role}@test.com",
            "role": role,
        },
    )


def _mock_autonomous_enabled():
    return patch("app.utils.config.is_autonomous_enabled", return_value=True)


class TestGetWorkflowPrStats(unittest.TestCase):
    def setUp(self):
        self.client, self.db_path, self.orig, self.orig_get_database_url, self.db_mod = (
            _make_client()
        )

    def tearDown(self):
        self.db_mod.adapt_sql = self.orig
        self.db_mod.get_database_url = self.orig_get_database_url
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("app.routes.autonomous.auto_repo")
    def test_pr_stats_workflow_not_found(self, mock_repo):
        mock_repo.get_workflow.return_value = None
        with _mock_autonomous_enabled(), _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/pr-stats")
        self.assertEqual(resp.status_code, 404)

    @patch("app.routes.autonomous.auto_repo")
    def test_pr_stats_access_denied_non_admin(self, mock_repo):
        mock_repo.get_workflow.return_value = _make_workflow(user_id=99)
        with _mock_autonomous_enabled(), _mock_auth(user_id=1, role="user"):
            resp = self.client.get("/api/autonomous/workflows/wf-1/pr-stats")
        self.assertEqual(resp.status_code, 403)

    @patch("app.routes.autonomous.auto_repo")
    def test_pr_stats_without_pr_returns_nulls(self, mock_repo):
        mock_repo.get_workflow.return_value = _make_workflow(github_pr_number=None)
        with _mock_autonomous_enabled(), _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/pr-stats")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {
                "success": True,
                "pr_number": None,
                "additions": None,
                "deletions": None,
                "changed_files": None,
            },
        )

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_pr_stats_returns_lightweight_counts(self, mock_repo, mock_gh_class):
        mock_repo.get_workflow.return_value = _make_workflow(github_pr_number=42)
        mock_gh = MagicMock()
        mock_gh.get_pr.return_value = {
            "number": 42,
            "additions": 20,
            "deletions": 3,
            "changedFiles": 4,
        }
        mock_gh_class.return_value = mock_gh

        with _mock_autonomous_enabled(), _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/pr-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {
                "success": True,
                "pr_number": 42,
                "additions": 20,
                "deletions": 3,
                "changed_files": 4,
            },
        )

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_pr_stats_failure_returns_null_counts(self, mock_repo, mock_gh_class):
        mock_repo.get_workflow.return_value = _make_workflow(github_pr_number=42)
        mock_gh = MagicMock()
        mock_gh.get_pr.side_effect = RuntimeError("gh failed")
        mock_gh_class.return_value = mock_gh

        with _mock_autonomous_enabled(), _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/pr-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.get_json(),
            {
                "success": True,
                "pr_number": 42,
                "additions": None,
                "deletions": None,
                "changed_files": None,
            },
        )
