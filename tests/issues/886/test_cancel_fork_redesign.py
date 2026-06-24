"""
Tests for cancel/fork redesign (Issue #886).

Covers:
- Cancel milestone with user feedback (required)
- Fork milestone creates independent workflow
- Fork copies milestones up to fork point
- Feedback injection into orchestrator prompts
- Resume with feedback via _do_wait()
- GET /forks endpoint
- POST /resume-with-feedback endpoint
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

# Modules that import is_postgresql/adapt_sql via "from ... import" and hold
# local references that patch.object on db_mod alone will NOT reach.  We must
# patch them in every module that has already performed the import.
_IS_PG_TARGETS = [
    "app.repositories.database",
    "app.repositories.autonomous_repo",
    "app.modules.workspace.autonomous",
    "app.modules.workspace.autonomous.orchestrator",
]

_ADAPT_SQL_TARGETS = [
    "app.repositories.database",
    "app.repositories.autonomous_repo",
]


def _patch_is_postgresql():
    """Return a list of patchers that force is_postgresql() → False everywhere."""
    patchers = []
    for mod_path in _IS_PG_TARGETS:
        mod = sys.modules.get(mod_path)
        if mod is not None and hasattr(mod, "is_postgresql"):
            patchers.append(patch.object(mod, "is_postgresql", return_value=False))
    return patchers


def _passthrough_sql(q):
    """Return SQL query unchanged for SQLite compatibility."""
    return q


def _replace_adapt_sql():
    """Replace adapt_sql with passthrough in all target modules; return originals."""
    originals = {}
    for mod_path in _ADAPT_SQL_TARGETS:
        mod = sys.modules.get(mod_path)
        if mod is not None and hasattr(mod, "adapt_sql"):
            originals[mod_path] = mod.adapt_sql
            mod.adapt_sql = _passthrough_sql
    return originals


def _restore_adapt_sql(originals):
    """Restore original adapt_sql functions."""
    for mod_path, orig_fn in originals.items():
        mod = sys.modules.get(mod_path)
        if mod is not None:
            mod.adapt_sql = orig_fn


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables.

    Patches is_postgresql and adapt_sql in *all* modules that hold local
    references so the test is isolated regardless of import order.
    """
    is_pg_patchers = _patch_is_postgresql()
    for p in is_pg_patchers:
        p.start()
    adapt_originals = _replace_adapt_sql()
    try:
        db_path = str(tmp_path / "test_cancel_fork.db")
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
                "INSERT INTO users (username, email, password_hash, role) " "VALUES (?, ?, ?, ?)",
                ("admin", "admin@test.com", "hash123", "admin"),
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
        _restore_adapt_sql(adapt_originals)
        for p in reversed(is_pg_patchers):
            p.stop()
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
        "workflow_id": "wf-fork-new",
        "title": "Original [Fork]",
        "status": "pending",
        "cli_tool": "claude-code",
    }
    repo.get_workflow.return_value = None
    repo.list_workflows.return_value = []
    repo.list_milestones.return_value = []
    repo.create_milestone.return_value = {
        "milestone_id": "ms-mock",
        "workflow_id": "wf-mock",
    }
    repo.create_event.return_value = {"id": 1}
    repo.update_workflow.return_value = {}
    repo.delete_workflow.return_value = None
    repo.get_milestone.return_value = None
    repo.cancel_milestones_after.return_value = []
    repo.list_forks.return_value = []
    repo.copy_milestones_to_workflow.return_value = 3
    return repo


def _make_workflow(**overrides):
    """Create a sample workflow dict."""
    wf = {
        "workflow_id": "wf-001",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "developing",
        "requirements_text": "Build a feature",
        "project_path": "/tmp/test",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "default",
        "branch_name": "feature/test",
        "branch_strategy": "branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 2,
        "max_pr_review_rounds": 2,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "parent_workflow_id": None,
        "fork_milestone_id": None,
        "user_feedback": "",
        "original_branch_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "paused_at": None,
    }
    wf.update(overrides)
    return wf


def _make_milestone(**overrides):
    """Create a sample milestone dict."""
    ms = {
        "milestone_id": "ms-001",
        "workflow_id": "wf-001",
        "phase": "development",
        "dev_round": 1,
        "round_number": 1,
        "milestone_type": "dev_completed",
        "status": "completed",
        "title": "Development round 1 completed",
        "description": "",
        "session_id": "sess-001",
        "review_session_id": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_comment_id": "",
        "commit_shas": "abc123",
        "diff_stats": '{"additions": 10, "deletions": 2, "files": 3, "commits": 1}',
        "result_summary": "Implemented feature",
        "plan_content": "",
        "review_content": "",
        "error_message": "",
        "parent_milestone_id": "",
        "fork_branch": "",
        "fork_workflow_id": "",
        "metadata": "{}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    ms.update(overrides)
    return ms


# ── Cancel Milestone with Feedback Tests ──────────────────────────────────


class TestCancelMilestoneWithFeedback:
    """Tests for POST /api/autonomous/workflows/<id>/milestones/<id>/cancel."""

    def test_cancel_requires_feedback(self, client):
        """Cancel endpoint returns 400 if user_feedback is missing."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/cancel",
                    json={},
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "feedback" in data.get("error", "").lower()

    def test_cancel_empty_feedback_rejected(self, client):
        """Cancel endpoint returns 400 if user_feedback is empty string."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/cancel",
                    json={"user_feedback": "   "},
                )

        assert resp.status_code == 400

    def test_cancel_stores_feedback_on_workflow(self, client):
        """Cancel stores user_feedback on the workflow and sets wait phase."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()
        repo.cancel_milestones_after.return_value = []

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/cancel",
                    json={"user_feedback": "Please focus on authentication module"},
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify update_workflow was called with feedback and wait state
        update_calls = repo.update_workflow.call_args_list
        assert len(update_calls) >= 1
        # Find the call that sets user_feedback
        feedback_found = False
        for call in update_calls:
            args = call[0]
            if len(args) >= 2:
                updates = args[1] if isinstance(args[1], dict) else {}
                if "user_feedback" in updates:
                    assert updates["user_feedback"] == "Please focus on authentication module"
                    feedback_found = True
        assert feedback_found, "update_workflow should set user_feedback"

    def test_cancel_creates_requirement_received_milestone(self, client):
        """Cancel creates a requirement_received milestone."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/cancel",
                    json={"user_feedback": "Refactor the API layer"},
                )

        assert resp.status_code == 200

        # Verify create_milestone was called with requirement_received type
        create_calls = repo.create_milestone.call_args_list
        types_found = []
        for call in create_calls:
            args = call[0]
            # create_milestone takes a single dict argument
            ms_data = args[0] if len(args) >= 1 and isinstance(args[0], dict) else {}
            if "milestone_type" in ms_data:
                types_found.append(ms_data["milestone_type"])
        assert "requirement_received" in types_found


# ── Fork Milestone Tests ──────────────────────────────────────────────────


class TestForkMilestone:
    """Tests for POST /api/autonomous/workflows/<id>/milestones/<id>/fork."""

    def test_fork_creates_new_workflow(self, client):
        """Fork creates a new independent workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/fork",
                    json={
                        "user_feedback": "Try microservices approach",
                        "pause_original": True,
                        "branch_name": "fork/microservices",
                    },
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify create_workflow was called (new independent workflow)
        repo.create_workflow.assert_called_once()
        create_args = repo.create_workflow.call_args[0]
        new_wf = create_args[0] if isinstance(create_args[0], dict) else create_args[1]
        assert new_wf.get("parent_workflow_id") == "wf-001"
        assert new_wf.get("fork_milestone_id") == "ms-001"
        assert new_wf.get("user_feedback") == "Try microservices approach"

    def test_fork_copies_milestones(self, client):
        """Fork copies milestones up to fork point."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/fork",
                    json={
                        "user_feedback": "Alternative approach",
                        "pause_original": False,
                    },
                )

        assert resp.status_code == 200

        # Verify copy_milestones_to_workflow was called
        repo.copy_milestones_to_workflow.assert_called_once()

    def test_fork_pause_original(self, client):
        """Fork with pause_original=true pauses the original workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/fork",
                    json={
                        "user_feedback": "Alternative direction",
                        "pause_original": True,
                    },
                )

        assert resp.status_code == 200

        # Check that update_workflow was called with paused status
        update_calls = repo.update_workflow.call_args_list
        paused_found = False
        for call in update_calls:
            args = call[0]
            if len(args) >= 2:
                updates = args[1] if isinstance(args[1], dict) else {}
                if updates.get("status") == "paused":
                    paused_found = True
        assert paused_found, "Original workflow should be paused"

    def test_fork_creates_forked_milestone_on_parent(self, client):
        """Fork creates a workflow_forked milestone on the parent workflow."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/fork",
                    json={
                        "user_feedback": "Try different approach",
                        "pause_original": False,
                    },
                )

        assert resp.status_code == 200

        # Verify create_milestone was called with workflow_forked type
        create_calls = repo.create_milestone.call_args_list
        types_found = []
        for call in create_calls:
            args = call[0]
            ms_data = args[0] if len(args) >= 1 and isinstance(args[0], dict) else {}
            if "milestone_type" in ms_data:
                types_found.append(ms_data["milestone_type"])
        assert "workflow_forked" in types_found

    def test_fork_forces_worktree_strategy(self, client):
        """Fork workflow uses worktree branch strategy."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.get_milestone.return_value = _make_milestone()

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/milestones/ms-001/fork",
                    json={
                        "user_feedback": "Parallel approach",
                        "pause_original": False,
                    },
                )

        assert resp.status_code == 200

        # Verify the new workflow has branch_strategy = "worktree"
        create_args = repo.create_workflow.call_args[0]
        new_wf = create_args[0] if isinstance(create_args[0], dict) else create_args[1]
        assert new_wf.get("branch_strategy") == "worktree"


# ── Fork Listing Tests ────────────────────────────────────────────────────


class TestForkListing:
    """Tests for GET /api/autonomous/workflows/<id>/forks."""

    def test_get_forks_returns_children(self, client):
        """GET /forks returns child workflows."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.list_forks.return_value = [
            _make_workflow(
                workflow_id="wf-fork-1",
                parent_workflow_id="wf-001",
                title="Test [Fork]",
            )
        ]

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get(
                    "/api/autonomous/workflows/wf-001/forks",
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["forks"]) == 1
        assert data["forks"][0]["workflow_id"] == "wf-fork-1"

    def test_get_forks_empty(self, client):
        """GET /forks returns empty list when no forks exist."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow()
        repo.list_forks.return_value = []

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.get(
                    "/api/autonomous/workflows/wf-001/forks",
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["forks"] == []


# ── Resume with Feedback Tests ────────────────────────────────────────────


class TestResumeWithFeedback:
    """Tests for POST /api/autonomous/workflows/<id>/resume-with-feedback."""

    def test_resume_with_feedback(self, client):
        """Resume endpoint updates feedback and sets waiting status."""
        repo = _make_repo()
        repo.get_workflow.return_value = _make_workflow(status="waiting", current_phase="wait")

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows/wf-001/resume-with-feedback",
                    json={"user_feedback": "Focus on performance optimization"},
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify update_workflow was called with feedback
        update_calls = repo.update_workflow.call_args_list
        feedback_found = False
        for call in update_calls:
            args = call[0]
            if len(args) >= 2:
                updates = args[1] if isinstance(args[1], dict) else {}
                if updates.get("user_feedback") == "Focus on performance optimization":
                    feedback_found = True
        assert feedback_found


# ── Orchestrator Feedback Injection Tests ──────────────────────────────────


class TestOrchestratorFeedbackInjection:
    """Tests for _get_user_feedback_prompt and prompt injection."""

    def test_feedback_prompt_injection(self):
        """_get_user_feedback_prompt returns formatted feedback text."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="Focus on testing and code coverage")

        prompt = orch._get_user_feedback_prompt(wf)
        assert "Focus on testing and code coverage" in prompt
        assert "用户反馈" in prompt

    def test_feedback_prompt_empty_when_no_feedback(self):
        """_get_user_feedback_prompt returns empty string when no feedback."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="")

        prompt = orch._get_user_feedback_prompt(wf)
        assert prompt == ""

    def test_feedback_prompt_empty_when_whitespace_only(self):
        """_get_user_feedback_prompt returns empty string for whitespace."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="   ")

        prompt = orch._get_user_feedback_prompt(wf)
        assert prompt == ""


# ── Repository Integration Tests ──────────────────────────────────────────


class TestForkRepoIntegration:
    """Integration tests for fork-related repository methods with real DB."""

    def test_list_forks_empty(self, auto_db):
        """list_forks returns empty list when no forks exist."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create a parent workflow first
        wf_data = _make_workflow()
        repo.create_workflow(wf_data)

        forks = repo.list_forks("wf-001")
        assert forks == []

    def test_create_and_list_forks(self, auto_db):
        """list_forks returns child workflows after creation."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create parent
        parent = _make_workflow()
        repo.create_workflow(parent)

        # Create fork child
        child = _make_workflow(
            workflow_id="wf-fork-001",
            title="Test [Fork]",
            parent_workflow_id="wf-001",
            fork_milestone_id="ms-001",
        )
        repo.create_workflow(child)

        forks = repo.list_forks("wf-001")
        assert len(forks) == 1
        assert forks[0]["workflow_id"] == "wf-fork-001"
        assert forks[0]["parent_workflow_id"] == "wf-001"

    def test_copy_milestones_to_workflow(self, auto_db):
        """copy_milestones_to_workflow copies milestones with new IDs."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create source workflow
        src = _make_workflow()
        repo.create_workflow(src)

        # Create milestones for source
        ms1 = _make_milestone(milestone_id="ms-1", phase="planning", milestone_type="plan_created")
        ms2 = _make_milestone(
            milestone_id="ms-2", phase="development", milestone_type="dev_completed"
        )
        ms3 = _make_milestone(milestone_id="ms-3", phase="development", milestone_type="tests_run")
        repo.create_milestone(ms1)
        repo.create_milestone(ms2)
        repo.create_milestone(ms3)

        # Create destination workflow
        dst = _make_workflow(workflow_id="wf-fork-dst", title="Fork Target")
        repo.create_workflow(dst)

        # Copy up to and including ms-2
        copied = repo.copy_milestones_to_workflow("wf-001", "wf-fork-dst", "ms-2")
        assert len(copied) == 2

        # Verify copied milestones belong to destination
        dst_milestones = repo.list_milestones("wf-fork-dst")
        assert len(dst_milestones) == 2
        # The original milestones have new IDs (UUIDs)
        original_ids = {"ms-1", "ms-2"}
        copied_ids = {ms["milestone_id"] for ms in dst_milestones}
        assert copied_ids.isdisjoint(original_ids), "Copied milestones should have new IDs"

    def test_create_milestone_persists_fork_workflow_id(self, auto_db):
        """Fork marker milestones persist fork_workflow_id for timeline fork visualization."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        repo.create_workflow(_make_workflow())
        created = repo.create_milestone(
            _make_milestone(
                milestone_id="ms-fork",
                milestone_type="workflow_forked",
                title="Forked to new workflow",
                fork_branch="fork/from-ms-fork",
                fork_workflow_id="wf-fork-001",
            )
        )

        assert created["fork_workflow_id"] == "wf-fork-001"

        fetched = repo.get_milestone("ms-fork")
        assert fetched is not None
        assert fetched["fork_workflow_id"] == "wf-fork-001"
