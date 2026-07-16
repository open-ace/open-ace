"""
Batch 4 tests — Diff API endpoint, status filter.

Tests for:
- GET /api/autonomous/workflows/<id>/milestones/<mid>/diff
- Workflow list status filter query parameter
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _make_workflow(**overrides):
    """Create a minimal workflow dict for testing."""
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


def _make_milestone(**overrides):
    """Create a minimal milestone dict for testing."""
    base = {
        "milestone_id": "ms-1",
        "workflow_id": "wf-1",
        "phase": "development",
        "dev_round": 1,
        "round_number": 1,
        "milestone_type": "development",
        "status": "completed",
        "title": "Dev Round 1",
        "description": "",
        "session_id": "sess-1",
        "review_session_id": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_comment_id": "",
        "commit_shas": "",
        "diff_stats": "",
        "result_summary": "Done",
        "plan_content": "",
        "review_content": "",
        "error_message": "",
        "parent_milestone_id": "",
        "fork_branch": "",
        "metadata": "",
        "started_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:00:00",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _make_client():
    """Create Flask test client with test DB and mock auth."""
    import app.repositories.database as db_mod

    db_path = tempfile.mktemp(suffix=".db")
    orig = db_mod.adapt_sql
    db_mod.adapt_sql = lambda sql: sql

    db = db_mod.Database(db_path)
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)"
            )
            cursor.execute(
                "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                ("admin", "admin@test.com", "hash123", "admin"),
            )
            from app.repositories.schema_init import load_schema_from_file

            load_schema_from_file(db_url=db.db_url, dialect="sqlite")
            conn.commit()
    finally:
        pass

    from app import create_app

    app = create_app({"TESTING": True})
    c = app.test_client()
    c.set_cookie("session_token", "test-token")
    return c, db_path, orig, db_mod


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


class TestGetMilestoneDiff(unittest.TestCase):
    """Tests for GET /api/autonomous/workflows/<id>/milestones/<mid>/diff."""

    def setUp(self):
        self.client, self.db_path, self.orig, self.db_mod = _make_client()

    def tearDown(self):
        self.db_mod.adapt_sql = self.orig
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_workflow_not_found(self, mock_repo):
        """Return 404 if workflow does not exist."""
        mock_repo.get_workflow.return_value = None
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn("not found", data["error"].lower())

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_access_denied_non_admin(self, mock_repo):
        """Return 403 if non-admin tries to access another user's workflow."""
        mock_repo.get_workflow.return_value = _make_workflow(user_id=99)
        with _mock_auth(user_id=1, role="user"):
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        self.assertEqual(resp.status_code, 403)

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_milestone_not_found(self, mock_repo):
        """Return 404 if milestone does not exist."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = None
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-missing/diff")
        self.assertEqual(resp.status_code, 404)

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_milestone_wrong_workflow(self, mock_repo):
        """Return 404 if milestone belongs to different workflow."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(workflow_id="other-wf")
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        self.assertEqual(resp.status_code, 404)

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_empty_commit_shas(self, mock_repo):
        """Return empty diff when milestone has no commits."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="")
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["diff"], "")

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_with_json_array_shas(self, mock_repo, mock_gh_class):
        """Return concatenated diffs for commits in JSON array format."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(
            commit_shas='["abc123def", "456789ghi"]'
        )

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.side_effect = [
            "diff --git a/file1.py b/file1.py\n+added line",
            "diff --git a/file2.py b/file2.py\n-removed line",
        ]
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("abc123de", data["diff"])
        self.assertIn("456789gh", data["diff"])
        self.assertIn("file1.py", data["diff"])
        self.assertIn("file2.py", data["diff"])

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_comma_separated_shas(self, mock_repo, mock_gh_class):
        """Handle comma-separated commit SHAs (not JSON array)."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123,def456")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.return_value = "some diff"
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(mock_gh.get_commit_diff.call_count, 2)

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_github_ops_error_graceful(self, mock_repo, mock_gh_class):
        """Gracefully handle GitHubOps errors — return empty diff for failed commits."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.side_effect = Exception("git error")
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["diff"], "")

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_uses_worktree_path_preferred(self, mock_repo, mock_gh_class):
        """Use worktree_path over project_path when both exist."""
        mock_repo.get_workflow.return_value = _make_workflow(
            project_path="/tmp/project",
            worktree_path="/tmp/worktree",
        )
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.return_value = "diff content"
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        mock_gh_class.assert_called_once_with("/tmp/worktree")

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_falls_back_to_project_path(self, mock_repo, mock_gh_class):
        """Use project_path when worktree_path is empty."""
        mock_repo.get_workflow.return_value = _make_workflow(
            project_path="/tmp/project",
            worktree_path="",
        )
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.return_value = "diff content"
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        mock_gh_class.assert_called_once_with("/tmp/project")

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_single_sha_string(self, mock_repo):
        """Handle a single commit SHA string (not array or comma-separated)."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123def456")

        with _mock_auth():
            with patch("app.modules.workspace.autonomous.github_ops.GitHubOps") as mock_gh_class:
                mock_gh = MagicMock()
                mock_gh.get_commit_diff.return_value = "diff content"
                mock_gh_class.return_value = mock_gh

                resp = self.client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(mock_gh.get_commit_diff.call_count, 1)


class TestWorkflowListStatusFilter(unittest.TestCase):
    """Tests for workflow list status filter query parameter."""

    def setUp(self):
        self.client, self.db_path, self.orig, self.db_mod = _make_client()

    def tearDown(self):
        self.db_mod.adapt_sql = self.orig
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("app.routes.autonomous.auto_repo")
    def test_list_with_status_filter(self, mock_repo):
        """Status filter is passed through to repo."""
        mock_repo.list_workflows.return_value = []
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows?status=completed")
        self.assertEqual(resp.status_code, 200)
        mock_repo.list_workflows.assert_called_once()

    @patch("app.routes.autonomous.auto_repo")
    def test_list_without_status_filter(self, mock_repo):
        """List all workflows when no status filter provided."""
        mock_repo.list_workflows.return_value = []
        with _mock_auth():
            resp = self.client.get("/api/autonomous/workflows")
        self.assertEqual(resp.status_code, 200)
        mock_repo.list_workflows.assert_called_once()


if __name__ == "__main__":
    unittest.main()
