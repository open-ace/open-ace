"""Tests for Issue #740 Batch 1 — Session manager wiring, remote null safety, and process termination.

Covers:
- SessionManager is passed to AutonomousAgentRunner at orchestrator init
- _run_agent wrapper tracks session_id for cancellation
- cancel_current_task() stops the running agent session
- Remote execution null guard for session_manager
- Scheduler exposes running orchestrator instances
- Stop/pause API routes cancel running agent tasks
"""

import threading
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.services.autonomous_scheduler import AutonomousScheduler as AutonomousSchedulerForTest

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
    }
    base.update(overrides)
    return base


def _make_agent_result(session_id="sess-abc123", success=True, text="Done", tokens=100, error=None):
    return AgentTaskResult(
        session_id=session_id,
        response_text=text,
        total_tokens=tokens,
        total_input_tokens=tokens // 2,
        total_output_tokens=tokens // 2,
        success=success,
        error=error,
    )


def _make_orchestrator(wf_data):
    """Create orchestrator with mocked dependencies."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls,
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
        mock_sm_cls.return_value = MagicMock()

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo

    # Mock emitter and runner
    orch.emitter = MagicMock()
    orch._runner = MagicMock()
    orch._runner.run_agent_task.return_value = _make_agent_result()
    orch._runner.stop_session = MagicMock()

    # Mock GitHubOps
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


# ── Test: SessionManager wiring ──────────────────────────────────────


class TestSessionManagerWiring:
    """Verify SessionManager is passed to AutonomousAgentRunner."""

    def test_session_manager_passed_to_runner(self):
        """SessionManager should be instantiated and passed to the runner."""
        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"),
            patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls,
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousAgentRunner"
            ) as mock_runner_cls,
        ):
            mock_sm = MagicMock()
            mock_sm_cls.return_value = mock_sm

            from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

            AutonomousOrchestrator("test-wf-id")

            mock_runner_cls.assert_called_once_with(session_manager=mock_sm)

    def test_session_manager_none_triggers_default(self):
        """Without our fix, runner would get session_manager=None (regression guard)."""
        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"),
            patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls,
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousAgentRunner"
            ) as mock_runner_cls,
        ):
            mock_sm = MagicMock()
            mock_sm_cls.return_value = mock_sm
            mock_runner_cls.return_value = MagicMock()

            from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

            AutonomousOrchestrator("test-wf-id")

            # Verify the keyword arg is present and is our mock
            call_kwargs = mock_runner_cls.call_args[1]
            assert "session_manager" in call_kwargs
            assert call_kwargs["session_manager"] is mock_sm

    def test_current_session_id_initially_none(self):
        """_current_session_id should start as None."""
        wf = _make_workflow()
        orch, _ = _make_orchestrator(wf)
        assert orch._current_session_id is None


# ── Test: _run_agent wrapper ─────────────────────────────────────────


class TestRunAgentWrapper:
    """Verify _run_agent tracks session_id after each call."""

    def test_run_agent_tracks_session_id(self):
        """After _run_agent completes, _current_session_id should be set."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        expected_session = "sess-tracked-123"
        orch._runner.run_agent_task.return_value = _make_agent_result(session_id=expected_session)

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build X"},
        ]

        # Call _do_development which uses _run_agent
        orch._do_development(wf)

        assert orch._current_session_id == expected_session

    def test_run_agent_updates_on_each_call(self):
        """_current_session_id should update with each _run_agent call."""
        wf = _make_workflow(current_phase="planning", status="planning", current_round=1)
        orch, mock_repo = _make_orchestrator(wf)

        # Planning calls _run_agent twice (plan + review)
        orch._runner.run_agent_task.side_effect = [
            _make_agent_result(session_id="sess-plan-1"),
            _make_agent_result(session_id="sess-review-1"),
        ]
        mock_repo.list_milestones.return_value = []

        orch._do_planning(wf)

        # Should track the last session_id
        assert orch._current_session_id == "sess-review-1"

    def test_session_id_available_before_run_agent_task_returns(self):
        """session_id must be set BEFORE run_agent_task returns (Critical fix).

        This verifies that cancel_current_task() can see the session_id
        while the agent is still executing.
        """
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        captured_session_id = None

        def fake_run_agent_task(**kwargs):
            nonlocal captured_session_id
            # Simulate: during execution, the session_id should already be set
            captured_session_id = orch._current_session_id
            return _make_agent_result(session_id=kwargs.get("session_id", "default"))

        orch._runner.run_agent_task.side_effect = fake_run_agent_task
        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "X"},
        ]

        orch._do_development(wf)

        # session_id was available DURING execution, not just after
        assert captured_session_id is not None
        assert orch._current_session_id == captured_session_id

    def test_run_agent_passes_pre_generated_session_id(self):
        """_run_agent should pass the pre-generated session_id to run_agent_task."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        orch._runner.run_agent_task.return_value = _make_agent_result()
        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "X"},
        ]

        orch._do_development(wf)

        # Verify session_id was passed as a parameter
        call_kwargs = orch._runner.run_agent_task.call_args[1]
        assert "session_id" in call_kwargs
        assert call_kwargs["session_id"] is not None


# ── Test: cancel_current_task ────────────────────────────────────────


class TestCancelCurrentTask:
    """Verify cancel_current_task stops the running session."""

    def test_cancel_stops_session(self):
        """cancel_current_task should call runner.stop_session."""
        wf = _make_workflow()
        orch, _ = _make_orchestrator(wf)

        orch._current_session_id = "sess-to-cancel"
        orch.cancel_current_task()

        orch._runner.stop_session.assert_called_once_with("sess-to-cancel")
        assert orch._current_session_id is None

    def test_cancel_noop_when_no_session(self):
        """cancel_current_task should be a no-op when no session is active."""
        wf = _make_workflow()
        orch, _ = _make_orchestrator(wf)

        assert orch._current_session_id is None
        orch.cancel_current_task()

        orch._runner.stop_session.assert_not_called()

    def test_cancel_handles_stop_error(self):
        """cancel_current_task should handle exceptions from stop_session."""
        wf = _make_workflow()
        orch, _ = _make_orchestrator(wf)

        orch._current_session_id = "sess-error"
        orch._runner.stop_session.side_effect = OSError("Process already dead")

        # Should not raise
        orch.cancel_current_task()

        # Session ID should still be cleared
        assert orch._current_session_id is None

    def test_cancel_then_clears_session_id(self):
        """After cancel, _current_session_id must be None."""
        wf = _make_workflow()
        orch, _ = _make_orchestrator(wf)

        orch._current_session_id = "sess-clear-test"
        orch.cancel_current_task()

        assert orch._current_session_id is None


# ── Test: Remote execution null guard ────────────────────────────────


class TestRemoteNullGuard:
    """Verify remote execution handles missing session_manager gracefully."""

    def test_remote_returns_error_when_no_session_manager(self):
        """_run_remote should return error if session_manager is None."""
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner(
            session_manager=None,
            remote_session_manager=MagicMock(),
        )

        # Create a mock remote_session_manager that returns success
        runner.remote_session_manager.create_remote_session.return_value = {
            "success": True,
            "session_id": "remote-sess-1",
        }
        runner.remote_session_manager.send_message.return_value = None

        result = runner._run_remote(
            session_id="remote-sess-1",
            cli_tool="claude-code",
            model="test-model",
            project_path="/tmp/test",
            prompt="Do something",
            remote_machine_id="machine-1",
            permission_mode="auto-edit",
            timeout=10,
        )

        assert result.success is False
        assert "session manager" in result.error.lower() or "not available" in result.error.lower()

    def test_remote_works_with_session_manager(self):
        """_run_remote should work when session_manager is provided."""
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        mock_sm = MagicMock()
        mock_sm.get_session.return_value = {
            "status": "completed",
            "total_tokens": 500,
            "total_input_tokens": 250,
            "total_output_tokens": 250,
        }

        runner = AutonomousAgentRunner(
            session_manager=mock_sm,
            remote_session_manager=MagicMock(),
        )

        runner.remote_session_manager.create_remote_session.return_value = {
            "success": True,
            "session_id": "remote-sess-2",
        }
        runner.remote_session_manager.send_message.return_value = None

        result = runner._run_remote(
            session_id="remote-sess-2",
            cli_tool="claude-code",
            model="test-model",
            project_path="/tmp/test",
            prompt="Do something",
            remote_machine_id="machine-1",
            permission_mode="auto-edit",
            timeout=10,
        )

        assert result.success is True
        assert result.total_tokens == 500

    def test_remote_returns_error_when_no_remote_session_manager(self):
        """_run_remote should return error when remote_session_manager is None."""
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner(
            session_manager=MagicMock(),
            remote_session_manager=None,
        )

        result = runner._run_remote(
            session_id="remote-sess-3",
            cli_tool="claude-code",
            model="test-model",
            project_path="/tmp/test",
            prompt="Do something",
            remote_machine_id="machine-1",
            permission_mode="auto-edit",
            timeout=10,
        )

        assert result.success is False
        assert "remote session manager" in result.error.lower()

    def test_remote_session_can_be_cancelled_via_stop_session(self):
        """stop_session should signal cancellation to remote sessions."""
        import threading
        import time

        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        mock_sm = MagicMock()

        # Session stays active so the loop keeps running
        mock_sm.get_session.return_value = {"status": "active"}

        runner = AutonomousAgentRunner(
            session_manager=mock_sm,
            remote_session_manager=MagicMock(),
        )

        runner.remote_session_manager.create_remote_session.return_value = {
            "success": True,
            "session_id": "remote-sess-cancel",
        }
        runner.remote_session_manager.send_message.return_value = None

        # Schedule cancellation after _run_remote has registered its tracker
        def cancel_later():
            time.sleep(0.2)
            tracker = runner._local_sessions.get("remote-sess-cancel")
            if tracker:
                tracker._stopped.set()

        threading.Thread(target=cancel_later, daemon=True).start()

        result = runner._run_remote(
            session_id="remote-sess-cancel",
            cli_tool="claude-code",
            model="test-model",
            project_path="/tmp/test",
            prompt="Do something",
            remote_machine_id="machine-1",
            permission_mode="auto-edit",
            timeout=10,
        )

        assert result.success is False
        assert "cancelled" in result.error.lower()


# ── Test: Scheduler orchestrator registry ────────────────────────────


class TestSchedulerOrchestratorRegistry:
    """Verify scheduler exposes running orchestrator instances."""

    def test_get_running_orchestrator_returns_none_initially(self):
        """No orchestrator should be registered initially."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        result = scheduler.get_running_orchestrator("nonexistent-wf-id")
        assert result is None

    def test_get_running_orchestrator_returns_instance(self):
        """After _advance_single starts, orchestrator should be retrievable."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        mock_orch = MagicMock()

        # Simulate a running orchestrator
        scheduler._running_orchestrators["wf-in-progress"] = mock_orch

        result = scheduler.get_running_orchestrator("wf-in-progress")
        assert result is mock_orch

    def test_advance_single_registers_and_unregisters(self):
        """_advance_single should register and then unregister orchestrator."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-reg-test"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        ):
            mock_orch = MagicMock()
            mock_orch_cls.return_value = mock_orch

            scheduler._advance_single(wf_id)

            # After completion, orchestrator should be unregistered
            assert scheduler.get_running_orchestrator(wf_id) is None

    def test_advance_single_unregisters_on_error(self):
        """_advance_single should unregister orchestrator even on error."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-error-test"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        ):
            mock_orch = MagicMock()
            mock_orch.advance.side_effect = RuntimeError("boom")
            mock_orch_cls.return_value = mock_orch

            scheduler._advance_single(wf_id)

            # Should still be unregistered after error
            assert scheduler.get_running_orchestrator(wf_id) is None

    def test_orchestrator_removed_from_in_progress_on_success(self):
        """_advance_single should remove workflow from in_progress set."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-cleanup-test"
        scheduler._in_progress_ids.add(wf_id)

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        ):
            mock_orch_cls.return_value = MagicMock()
            scheduler._advance_single(wf_id)

            assert wf_id not in scheduler._in_progress_ids

    def test_orchestrator_removed_from_in_progress_on_error(self):
        """_advance_single should remove workflow from in_progress even on error."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-error-cleanup"
        scheduler._in_progress_ids.add(wf_id)

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        ):
            mock_orch = MagicMock()
            mock_orch.advance.side_effect = Exception("fail")
            mock_orch_cls.return_value = mock_orch

            scheduler._advance_single(wf_id)

            assert wf_id not in scheduler._in_progress_ids


# ── Test: Stop/Pause API cancellation ────────────────────────────────


class TestStopPauseCancelsTask:
    """Verify stop/pause API routes cancel running agent tasks."""

    def _make_client(self):
        """Create Flask test client with test DB."""
        import os as _os
        import tempfile

        import app.repositories.database as db_mod
        from app import create_app

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
                from app.modules.workspace.autonomous import get_ddl_statements

                for sql in get_ddl_statements():
                    cursor.execute(sql)
                conn.commit()
        finally:
            pass

        app = create_app({"TESTING": True})
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        return c, db_path, orig, db_mod, _os

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

    def test_stop_calls_cancel_running_task(self):
        """stop_workflow should call _cancel_running_task."""
        c, db_path, orig, db_mod, _os = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-stop-test",
            "user_id": 1,
            "status": "developing",
        }
        repo.update_workflow.return_value = None

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.routes.autonomous._cancel_running_task") as mock_cancel:
                    resp = c.post("/api/autonomous/workflows/wf-stop-test/stop")

        assert resp.status_code == 200
        mock_cancel.assert_called_once_with("wf-stop-test")

        # Cleanup
        try:
            db_mod.adapt_sql = orig
            _os.unlink(db_path)
        except OSError:
            pass

    def test_pause_calls_cancel_running_task(self):
        """pause_workflow should call _cancel_running_task."""
        c, db_path, orig, db_mod, _os = self._make_client()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-pause-test",
            "user_id": 1,
            "status": "developing",
        }
        repo.update_workflow.return_value = None

        with self._mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.routes.autonomous._cancel_running_task") as mock_cancel:
                    resp = c.post("/api/autonomous/workflows/wf-pause-test/pause")

        assert resp.status_code == 200
        mock_cancel.assert_called_once_with("wf-pause-test")

        # Cleanup
        try:
            db_mod.adapt_sql = orig
            _os.unlink(db_path)
        except OSError:
            pass

    def test_cancel_running_task_calls_scheduler(self):
        """_cancel_running_task should find and cancel the orchestrator."""
        mock_orch = MagicMock()

        with patch.object(AutonomousSchedulerForTest, "instance") as mock_instance_method:
            mock_sched = MagicMock()
            mock_instance_method.return_value = mock_sched
            mock_sched.get_running_orchestrator.return_value = mock_orch

            from app.routes.autonomous import _cancel_running_task

            _cancel_running_task("wf-cancel-test")

            mock_orch.cancel_current_task.assert_called_once()

    def test_cancel_running_task_handles_no_orchestrator(self):
        """_cancel_running_task should be safe when no orchestrator is running."""
        with patch.object(AutonomousSchedulerForTest, "instance") as mock_instance_method:
            mock_sched = MagicMock()
            mock_instance_method.return_value = mock_sched
            mock_sched.get_running_orchestrator.return_value = None

            from app.routes.autonomous import _cancel_running_task

            # Should not raise
            _cancel_running_task("wf-no-orch")

    def test_cancel_running_task_handles_exception(self):
        """_cancel_running_task should handle scheduler errors gracefully."""
        with patch.object(AutonomousSchedulerForTest, "instance") as mock_instance_method:
            mock_sched = MagicMock()
            mock_instance_method.return_value = mock_sched
            mock_sched.get_running_orchestrator.side_effect = RuntimeError("scheduler error")

            from app.routes.autonomous import _cancel_running_task

            # Should not raise
            _cancel_running_task("wf-error")


# ── Test: Integration — Full stop flow ───────────────────────────────


class TestIntegrationStopFlow:
    """Integration test: stop API → scheduler → orchestrator → agent stop."""

    def test_full_stop_flow(self):
        """Stop API should reach through to stop the agent subprocess."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        # Simulate agent running
        orch._current_session_id = "sess-running-123"

        # Create scheduler with orchestrator registered
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        scheduler._running_orchestrators[wf["workflow_id"]] = orch

        # Simulate stop flow
        retrieved = scheduler.get_running_orchestrator(wf["workflow_id"])
        assert retrieved is orch

        # Cancel the task
        retrieved.cancel_current_task()
        orch._runner.stop_session.assert_called_once_with("sess-running-123")
        assert orch._current_session_id is None

    def test_full_pause_flow(self):
        """Pause API should cancel the agent but keep the workflow for resume."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        orch._current_session_id = "sess-pause-me"

        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        scheduler._running_orchestrators[wf["workflow_id"]] = orch

        # Simulate pause flow
        retrieved = scheduler.get_running_orchestrator(wf["workflow_id"])
        retrieved.cancel_current_task()

        orch._runner.stop_session.assert_called_once_with("sess-pause-me")
        assert orch._current_session_id is None


# ── Test: Existing test compatibility ────────────────────────────────


class TestBackwardCompatibility:
    """Verify existing tests still work with new _run_agent wrapper."""

    def test_do_development_still_works(self):
        """_do_development should still function with _run_agent wrapper."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        # Should have called _runner.run_agent_task (through _run_agent wrapper)
        assert orch._runner.run_agent_task.called
        # Session ID should be tracked
        assert orch._current_session_id is not None

    def test_do_planning_still_works(self):
        """_do_planning should still function with _run_agent wrapper."""
        wf = _make_workflow(current_phase="planning", status="planning", current_round=1)
        orch, mock_repo = _make_orchestrator(wf)

        orch._runner.run_agent_task.side_effect = [
            _make_agent_result(session_id="sess-plan", text="# Plan\nStep 1"),
            _make_agent_result(session_id="sess-review", text="LGTM"),
        ]
        mock_repo.list_milestones.return_value = []

        orch._do_planning(wf)

        # Planning calls agent twice (plan + review)
        assert orch._runner.run_agent_task.call_count == 2
        # Session ID should be the last one (review)
        assert orch._current_session_id == "sess-review"
