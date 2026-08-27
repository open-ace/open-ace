"""Issue #740 — Milestone diff API and workflow list status filter (R1-migrated).

Migrated wholesale from tests/issues/740/test_batch4_diff_api.py (all 13 items
are route-bound).

R1 repair: the legacy hand-rolled ``_make_client()`` created a temp SQLite DB
but left ``DATABASE_URL`` untouched, so ``create_app``'s ``ensure_all_tables``
dialed the ambient (Postgres) config DB. Replaced with the canonical
``auto_db``/``client`` bootstrap from tests/integration/routes/test_autonomous_api.py
(tmp sqlite + ``DATABASE_URL`` env pin + ``user_repo`` rebind). Assertions
unchanged (the original ``unittest`` asserts were translated 1:1 to plain
``assert`` statements).
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

pytestmark = [pytest.mark.regression, pytest.mark.issue(740)]

# ── Helpers ──────────────────────────────────────────────────────────


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


# ── Canonical fixtures (tests/integration/routes/test_autonomous_api.py) ──


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
                from app.repositories.schema_init import load_schema_from_file

                # Create the FULL authoritative schema (incl. users.deleted_at) on the
                # empty DB FIRST, then seed. Do NOT hand-CREATE an old users table —
                # load_schema's CREATE TABLE IF NOT EXISTS will not add the missing
                # column to an already-existing table (legacy tests/issues escape hatch).
                load_schema_from_file(db_url=db.db_url, dialect="sqlite")
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "platform_admin"),
                )
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


@pytest.fixture
def client(auto_db, monkeypatch):
    """Create a Flask test client with session cookie set."""
    from app import create_app
    from app.repositories.user_repo import UserRepository

    # create_app()'s ensure_all_tables() picks its DB from get_database_url()
    # (DATABASE_URL), which defaults to the dev Postgres DB locally; and the
    # module-global user_repo (app.routes.autonomous) binds Database() once at
    # import. Point both at auto_db's seeded SQLite DB so the app and the route
    # share the fixture's database. monkeypatch auto-restores both.
    monkeypatch.setenv("DATABASE_URL", auto_db.db_url)
    # The create endpoint's module-level rate limiter (10/user/hour) is
    # process-global state: hits accumulated by earlier test files in the same
    # pytest process would 429 this file's own create requests (they run as
    # user_id=1 too). Clear the per-user hit log so each test starts with a
    # full budget; the limiter itself stays fully in effect within the test.
    monkeypatch.setattr("app.routes.autonomous._workflow_rate_limiter._hits", {})
    # These endpoint tests were written for single-tenant semantics: pin the
    # deployment mode so the default branch_strategy ("new-branch") is not
    # rejected by the multi-user _shared_checkout_rejection gate (#2021;
    # that rejection logic is separately unit-covered by
    # tests/unit/test_git_path_hardening.py).
    monkeypatch.setenv("OPENACE_ALLOW_SHARED_CHECKOUT", "1")
    app = create_app({"TESTING": True})
    monkeypatch.setattr("app.routes.autonomous.user_repo", UserRepository(db=auto_db))
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


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


@pytest.fixture(autouse=True)
def _stub_owner_user_lookup():
    """Stub the owner system_account resolution (kept from the legacy setUp).

    get_milestone_diff resolves the workflow owner's system_account via the
    module-level UserRepository (bound at import time to the DEFAULT
    database, not the temp DB) — stub it so the system_account assertion
    doesn't depend on whatever the ambient environment DB holds.
    """
    from app.repositories.user_repo import UserRepository

    with patch.object(
        UserRepository,
        "get_user_by_id",
        lambda self_, user_id: {
            "id": user_id,
            "username": "admin",
            "system_account": "admin",
            "role": "platform_admin",
            "tenant_id": None,
        },
    ):
        yield


class TestGetMilestoneDiff:
    """Tests for GET /api/autonomous/workflows/<id>/milestones/<mid>/diff."""

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_workflow_not_found(self, mock_repo, client):
        """Return 404 if workflow does not exist."""
        mock_repo.get_workflow.return_value = None
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "not found" in data["error"].lower()

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_access_denied_non_admin(self, mock_repo, client):
        """Return 403 if non-admin tries to access another user's workflow."""
        mock_repo.get_workflow.return_value = _make_workflow(user_id=99)
        with _mock_auth(user_id=1, role="user"):
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        assert resp.status_code == 403

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_milestone_not_found(self, mock_repo, client):
        """Return 404 if milestone does not exist."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = None
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-missing/diff")
        assert resp.status_code == 404

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_milestone_wrong_workflow(self, mock_repo, client):
        """Return 404 if milestone belongs to different workflow."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(workflow_id="other-wf")
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        assert resp.status_code == 404

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_empty_commit_shas(self, mock_repo, client):
        """Return empty diff when milestone has no commits."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="")
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"]
        assert data["diff"] == ""

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_with_json_array_shas(self, mock_repo, mock_gh_class, client):
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
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"]
        assert "abc123de" in data["diff"]
        assert "456789gh" in data["diff"]
        assert "file1.py" in data["diff"]
        assert "file2.py" in data["diff"]

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_comma_separated_shas(self, mock_repo, mock_gh_class, client):
        """Handle comma-separated commit SHAs (not JSON array)."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123,def456")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.return_value = "some diff"
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"]
        assert mock_gh.get_commit_diff.call_count == 2

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_github_ops_error_graceful(self, mock_repo, mock_gh_class, client):
        """Gracefully handle GitHubOps errors — return empty diff for failed commits."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123")

        mock_gh = MagicMock()
        mock_gh.get_commit_diff.side_effect = Exception("git error")
        mock_gh_class.return_value = mock_gh

        with _mock_auth():
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"]
        assert data["diff"] == ""

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_uses_worktree_path_preferred(self, mock_repo, mock_gh_class, client):
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
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        mock_gh_class.assert_called_once_with("/tmp/worktree", system_account="admin")

    @patch("app.modules.workspace.autonomous.github_ops.GitHubOps")
    @patch("app.routes.autonomous.auto_repo")
    def test_diff_falls_back_to_project_path(self, mock_repo, mock_gh_class, client):
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
            resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        mock_gh_class.assert_called_once_with("/tmp/project", system_account="admin")

    @patch("app.routes.autonomous.auto_repo")
    def test_diff_single_sha_string(self, mock_repo, client):
        """Handle a single commit SHA string (not array or comma-separated)."""
        mock_repo.get_workflow.return_value = _make_workflow()
        mock_repo.get_milestone.return_value = _make_milestone(commit_shas="abc123def456")

        with _mock_auth():
            with patch("app.modules.workspace.autonomous.github_ops.GitHubOps") as mock_gh_class:
                mock_gh = MagicMock()
                mock_gh.get_commit_diff.return_value = "diff content"
                mock_gh_class.return_value = mock_gh

                resp = client.get("/api/autonomous/workflows/wf-1/milestones/ms-1/diff")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"]
        assert mock_gh.get_commit_diff.call_count == 1


class TestWorkflowListStatusFilter:
    """Tests for workflow list status filter query parameter."""

    @patch("app.routes.autonomous.auto_repo")
    def test_list_with_status_filter(self, mock_repo, client):
        """Status filter is passed through to repo."""
        mock_repo.list_workflows.return_value = []
        mock_repo.count_workflows.return_value = 0
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows?status=completed")
        assert resp.status_code == 200
        mock_repo.list_workflows.assert_called_once()

    @patch("app.routes.autonomous.auto_repo")
    def test_list_without_status_filter(self, mock_repo, client):
        """List all workflows when no status filter provided."""
        mock_repo.list_workflows.return_value = []
        mock_repo.count_workflows.return_value = 0
        with _mock_auth():
            resp = client.get("/api/autonomous/workflows")
        assert resp.status_code == 200
        mock_repo.list_workflows.assert_called_once()
