"""Tests for Issue #740 Batch 2 — Idempotency, path validation, timeout config, retry limit.

Covers:
- Idempotency: _find_existing_milestone prevents duplicate milestone creation
- Path validation: rejects traversal, relative paths, unsafe chars
- Timeout: configurable via env var and per-workflow task_timeout field
- Retry limit: max 5 retries for failed workflows
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult

# ── Helpers ──────────────────────────────────────────────────────────


def _make_workflow(**overrides):
    """Create a minimal workflow dict for testing."""
    base = {
        "workflow_id": "test-wf-uuid",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "developing",
        "requirements_text": "Build a simple feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/test-project",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/test",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "retry_count": 0,
        "task_timeout": None,
    }
    base.update(overrides)
    return base


def _make_agent_result(session_id="sess-abc123", success=True, text="Done", tokens=100):
    return AgentTaskResult(
        session_id=session_id,
        response_text=text,
        total_tokens=tokens,
        total_input_tokens=tokens // 2,
        total_output_tokens=tokens // 2,
        success=success,
        error=None,
    )


def _make_orchestrator(wf_data):
    """Create orchestrator with mocked dependencies."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager"),
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo.update_workflow_tokens.return_value = None
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo

    orch.emitter = MagicMock()
    orch._runner = MagicMock()
    orch._runner.run_agent_task.return_value = _make_agent_result()
    orch._runner.stop_session = MagicMock()

    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps") as mock_gh_cls:
        mock_gh = MagicMock()
        mock_gh.get_current_commit.side_effect = ["abc123", "def456"]
        mock_gh.get_diff_stats.return_value = {
            "additions": 10,
            "deletions": 2,
            "files": 3,
            "commits": 1,
        }
        mock_gh.get_diff.return_value = "diff content"
        mock_gh.git_push.return_value = None
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_add_all.return_value = None
        mock_gh.git_commit.return_value = {"sha": "auto-sha", "message": "auto-commit"}
        mock_gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

    return orch, mock_repo


# ── Test: Idempotency ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _trusted_repo_boundary_for_wrapper_tests(monkeypatch):
    """Provide a trusted repo boundary for synthetic /tmp paths.

    Production local runs fail closed (#2271 repo-integrity guard) when they
    cannot snapshot a real Git repository; these tests use synthetic paths.
    Dedicated guardrail tests exercise the fail-closed behavior itself.
    """
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
    from app.repositories.user_repo import UserRepository

    # The dev/planning paths resolve the workflow owner via
    # UserRepository().get_user_by_id(), which binds its own Database() at
    # import time — the tests' orchestrator.Database patch does not cover it
    # and the lane DB may not be reachable from this process. These tests
    # don't assert on the user record.
    monkeypatch.setattr(
        UserRepository,
        "get_user_by_id",
        lambda self, user_id: {
            "id": user_id,
            "username": "owner",
            "system_account": None,
            "role": "platform_admin",
            "tenant_id": None,
        },
    )

    def snapshot(orchestrator, wf, workspace_type, system_account):
        if workspace_type != "local":
            return None
        context = orchestrator._resolve_effective_repo_context(wf)
        repo_path = context.get("repo_path", "/tmp/test-project")
        return {
            "context": context,
            "effective": {
                "repo_path": repo_path,
                "top_level": repo_path,
                "git_dir": f"{repo_path}/.git",
                "git_identity": "1:1",
                "common_dir": f"{repo_path}/.git",
                "common_identity": "1:1",
                "origin": "git@github.com:open-ace/open-ace.git",
            },
        }

    monkeypatch.setattr(AutonomousOrchestrator, "_snapshot_repo_context", snapshot)
    # Post-run verification would probe the same synthetic path with real git.
    monkeypatch.setattr(
        AutonomousOrchestrator, "_validate_repo_context_after_run", lambda self, *a, **k: ""
    )


class TestIdempotency:
    """Verify _find_existing_milestone prevents duplicate milestones."""

    def test_find_existing_milestone_returns_match(self):
        """Should return existing milestone with matching type/phase/round."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        existing_ms = {
            "milestone_id": "ms-existing",
            "milestone_type": "plan_created",
            "phase": "planning",
            "dev_round": 1,
            "round_number": 1,
            "status": "completed",
        }
        mock_repo.list_milestones.side_effect = [
            [],  # in_progress
            [existing_ms],  # completed
        ]

        result = orch._find_existing_milestone(
            phase="planning", milestone_type="plan_created", dev_round=1, round_number=1
        )

        assert result is not None
        assert result["milestone_id"] == "ms-existing"

    def test_find_existing_milestone_returns_none_when_empty(self):
        """Should return None when no matching milestone exists."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)
        mock_repo.list_milestones.return_value = []

        result = orch._find_existing_milestone(phase="planning", milestone_type="plan_created")

        assert result is None

    def test_find_existing_milestone_filters_by_dev_round(self):
        """Should not match milestones with different dev_round."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        wrong_round = {
            "milestone_type": "dev_started",
            "phase": "development",
            "dev_round": 1,
            "status": "completed",
        }
        mock_repo.list_milestones.side_effect = [[], [wrong_round]]

        result = orch._find_existing_milestone(
            phase="development", milestone_type="dev_started", dev_round=2
        )

        assert result is None

    def test_create_milestone_still_works(self):
        """_create_milestone should work as before when no existing."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        ms = orch._create_milestone(
            phase="development",
            milestone_type="dev_started",
            status="in_progress",
            title="Dev started",
        )

        assert ms is not None
        mock_repo.create_milestone.assert_called_once()


# ── Test: Path Validation ────────────────────────────────────────────


class TestPathValidation:
    """Verify project_path validation in create_workflow."""

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """These tests exercise path validation, not the quota/rate gate. Stub
        QuotaManager to allow-by-default so the (real, DB-backed) quota check
        doesn't reach the test's schema-less DB and spuriously 429 before path
        validation runs, reset the module-global workflow rate limiter
        (``_workflow_rate_limiter`` accumulates per-user hits across the whole
        pytest session, so without a reset these create-workflow requests are
        429'd once earlier tests in the shard exhaust the 10/hour budget), and
        stub the module-level ``user_repo.get_user_by_id`` (its db binds at
        import to a CI db with no users table, so the route's user lookup past
        path validation 500s once the reset lets valid-path requests through).
        The #2457 assert-429 / assert-500 cluster."""
        from app.routes.autonomous import _workflow_rate_limiter

        _workflow_rate_limiter._hits.clear()
        qmock = MagicMock()
        qmock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with (
            patch("app.modules.governance.quota_manager.QuotaManager", qmock),
            patch(
                "app.routes.autonomous.user_repo.get_user_by_id",
                return_value={
                    "id": 1,
                    "username": "admin",
                    "system_account": "",
                    "role": "admin",
                    "tenant_id": None,
                },
            ),
        ):
            yield

    def _make_client(self):
        """Create Flask test client with test DB."""
        import tempfile

        import app.repositories.database as db_mod

        db_path = tempfile.mktemp(suffix=".db")
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda sql: sql

        db = db_mod.Database(f"sqlite:///{db_path}")
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                from app.repositories.schema_init import load_schema_from_file

                # Let the authoritative schema create users: the old hand-rolled
                # CREATE TABLE drifted (no deleted_at/system_account columns —
                # the schema's partial indexes on those columns then failed).
                load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
                cursor.execute(
                    "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "admin"),
                )
                conn.commit()
        finally:
            pass

        from app import create_app

        app = create_app({"TESTING": True})
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        return c, db_path, orig, db_mod

    def _mock_auth(self, user_id=1, role="admin"):
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": user_id,
                "username": "admin" if role == "admin" else "testuser",
                "email": f"{role}@test.com",
                "role": role,
            },
        )

    def test_rejects_path_traversal(self):
        """Should reject paths with '..' traversal."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.create_workflow.return_value = {"workflow_id": "wf-1"}

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/../../etc/passwd",
                    },
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "path traversal" in data["error"].lower()

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_rejects_relative_path(self):
        """Should reject non-absolute paths."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.create_workflow.return_value = {"workflow_id": "wf-1"}

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "relative/path/project",
                    },
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "absolute" in data["error"].lower()

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_accepts_valid_absolute_path(self):
        """Should accept valid absolute paths."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.create_workflow.return_value = {
            "workflow_id": "wf-ok",
            "title": "test",
            "status": "pending",
            "cli_tool": "claude-code",
        }

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "/home/user/projects/my-app",
                    },
                )

        assert resp.status_code == 201

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_accepts_new_project_without_path(self):
        """Should accept when is_new_project=true (no path needed)."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.create_workflow.return_value = {
            "workflow_id": "wf-new",
            "title": "new proj",
            "status": "pending",
            "cli_tool": "claude-code",
        }

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "is_new_project": True,
                    },
                )

        assert resp.status_code == 201

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass


# ── Test: Timeout Configuration ──────────────────────────────────────


class TestTimeoutConfig:
    """Verify timeout is configurable via env var and workflow field."""

    def test_default_timeout_from_env(self):
        """DEFAULT_TASK_TIMEOUT should read from environment variable."""
        # The default is already set at import time, so we check it's an int
        from app.modules.workspace.autonomous.agent_runner import DEFAULT_TASK_TIMEOUT

        assert isinstance(DEFAULT_TASK_TIMEOUT, int)
        assert DEFAULT_TASK_TIMEOUT > 0

    def test_run_agent_injects_workflow_timeout(self):
        """_run_agent should inject task_timeout from workflow if set."""
        wf = _make_workflow(task_timeout=7200)
        orch, mock_repo = _make_orchestrator(wf)

        orch._run_agent(
            workflow_id="test-wf-uuid",
            cli_tool="claude-code",
            model="test",
            project_path="/tmp/test",
            prompt="do something",
        )

        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert call_kwargs.get("timeout") == 7200

    def test_run_agent_omits_timeout_when_not_set(self):
        """_run_agent should not add timeout when workflow has none."""
        wf = _make_workflow(task_timeout=None)
        orch, mock_repo = _make_orchestrator(wf)

        orch._run_agent(
            workflow_id="test-wf-uuid",
            cli_tool="claude-code",
            model="test",
            project_path="/tmp/test",
            prompt="do something",
        )

        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert "timeout" not in call_kwargs

    def test_run_agent_respects_explicit_timeout(self):
        """Explicit timeout kwarg should not be overridden by workflow."""
        wf = _make_workflow(task_timeout=9999)
        orch, mock_repo = _make_orchestrator(wf)

        orch._run_agent(
            workflow_id="test-wf-uuid",
            cli_tool="claude-code",
            model="test",
            project_path="/tmp/test",
            prompt="do something",
            timeout=600,
        )

        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert call_kwargs["timeout"] == 600


# ── Test: Retry Limit ────────────────────────────────────────────────


class TestRetryLimit:
    """Verify retry count limit for failed workflows."""

    def _make_client(self):
        """Create Flask test client with test DB."""
        import tempfile

        import app.repositories.database as db_mod

        db_path = tempfile.mktemp(suffix=".db")
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda sql: sql

        db = db_mod.Database(f"sqlite:///{db_path}")
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                from app.repositories.schema_init import load_schema_from_file

                # Let the authoritative schema create users: the old hand-rolled
                # CREATE TABLE drifted (no deleted_at/system_account columns —
                # the schema's partial indexes on those columns then failed).
                load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
                cursor.execute(
                    "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "admin"),
                )
                conn.commit()
        finally:
            pass

        from app import create_app

        app = create_app({"TESTING": True})
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        return c, db_path, orig, db_mod

    def _mock_auth(self, user_id=1, role="admin"):
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": user_id,
                "username": "admin",
                "email": f"{role}@test.com",
                "role": role,
            },
        )

    def test_retry_increments_count(self):
        """Retry should increment retry_count."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": 2,
        }
        repo.update_workflow.return_value = None

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post("/api/autonomous/workflows/wf-retry/retry")

        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["retry_count"] == 3

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_rejects_retry_over_limit(self):
        """Should reject retry when retry_count >= MAX_RETRY_COUNT."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-max-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": 5,
        }

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post("/api/autonomous/workflows/wf-max-retry/retry")

        assert resp.status_code == 400
        data = resp.get_json()
        assert "retry count" in data["error"].lower()

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_allows_retry_under_limit(self):
        """Should allow retry when retry_count < MAX_RETRY_COUNT."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-under-limit",
            "user_id": 1,
            "status": "failed",
            "retry_count": 4,
        }
        repo.update_workflow.return_value = None

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post("/api/autonomous/workflows/wf-under-limit/retry")

        assert resp.status_code == 200

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass

    def test_handles_none_retry_count(self):
        """Should treat None retry_count as 0."""
        c, db_path, orig, db_mod = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-no-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": None,
        }
        repo.update_workflow.return_value = None

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = c.post("/api/autonomous/workflows/wf-no-retry/retry")

        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["retry_count"] == 1

        try:
            db_mod.adapt_sql = orig
            os.unlink(db_path)
        except OSError:
            pass


# ── Test: Idempotency in _create_milestone (wired) ────────────────────


class TestIdempotencyWired:
    """Verify _create_milestone actually calls _find_existing_milestone."""

    def test_create_milestone_skips_when_existing(self):
        """_create_milestone should return existing milestone without creating a new one."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        existing_ms = {
            "milestone_id": "ms-existing",
            "milestone_type": "plan_created",
            "phase": "planning",
            "dev_round": 1,
            "round_number": 1,
            "status": "completed",
        }
        mock_repo.list_milestones.side_effect = [
            [],  # in_progress
            [existing_ms],  # completed
        ]

        result = orch._create_milestone(
            phase="planning",
            milestone_type="plan_created",
            dev_round=1,
            round_number=1,
            title="Plan",
        )

        # Should return the existing milestone, not create a new one
        assert result["milestone_id"] == "ms-existing"
        mock_repo.create_milestone.assert_not_called()

    def test_create_milestone_creates_when_no_existing(self):
        """_create_milestone should create new milestone when none exists."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)
        mock_repo.list_milestones.return_value = []

        result = orch._create_milestone(
            phase="development",
            milestone_type="dev_started",
            title="Dev started",
        )

        assert result is not None
        mock_repo.create_milestone.assert_called_once()


# ── Test: Timeout error handling ───────────────────────────────────────


class TestTimeoutErrorHandling:
    """Verify DEFAULT_TASK_TIMEOUT handles invalid env var gracefully."""

    def test_default_timeout_is_positive(self):
        """DEFAULT_TASK_TIMEOUT should always be a positive integer."""
        from app.modules.workspace.autonomous.agent_runner import DEFAULT_TASK_TIMEOUT

        assert isinstance(DEFAULT_TASK_TIMEOUT, int)
        assert DEFAULT_TASK_TIMEOUT > 0


# ── Test: _run_agent avoids extra DB queries ───────────────────────────


class TestRunAgentDbOptimization:
    """Verify _run_agent uses passed wf dict instead of re-querying."""

    def test_run_agent_uses_passed_wf_for_timeout(self):
        """_run_agent(wf=wf) should use the passed dict, not re-query DB."""
        wf = _make_workflow(task_timeout=5000)
        orch, mock_repo = _make_orchestrator(wf)

        mock_repo.get_workflow.reset_mock()

        orch._run_agent(
            wf=wf,
            workflow_id="test-wf-uuid",
            cli_tool="claude-code",
            model="test",
            project_path="/tmp/test",
            prompt="do something",
        )

        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert call_kwargs.get("timeout") == 5000

    def test_run_agent_without_wf_falls_back_to_db(self):
        """_run_agent() without wf should fall back to self.workflow."""
        wf = _make_workflow(task_timeout=3000)
        orch, mock_repo = _make_orchestrator(wf)

        orch._run_agent(
            workflow_id="test-wf-uuid",
            cli_tool="claude-code",
            model="test",
            project_path="/tmp/test",
            prompt="do something",
        )

        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert call_kwargs.get("timeout") == 3000
