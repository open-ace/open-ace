"""Tests for retry endpoint clearing stale scheduler in-progress state.

When an orchestrator thread exits abnormally (OOM kill, hard crash) without
running its ``finally`` cleanup, the workflow_id remains permanently in the
scheduler's ``_in_progress_ids`` set. The scheduler then skips this workflow
on every cycle, making retry ineffective — the DB status changes to "developing"
but no orchestrator ever advances it.

The fix: ``retry_workflow`` calls ``AutonomousScheduler.clear_in_progress()``
to remove the stale entry before updating the DB.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestRetryClearsInProgress:
    """Test that retry clears stale scheduler in-progress state."""

    @pytest.fixture
    def scheduler(self):
        """Create a real AutonomousScheduler instance with a stuck workflow_id."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        sched = AutonomousScheduler()
        wf_id = "stuck-workflow-123"
        sched._in_progress_ids.add(wf_id)
        sched._in_progress_workspaces.add("/fake/workspace")
        sched._in_progress_branches.add("auto-dev/stuck")
        sched._in_progress_batch_ids.add("batch-1")
        sched._running_orchestrators[wf_id] = MagicMock()
        return sched, wf_id

    @pytest.fixture
    def scheduler_with_wf(self):
        """Create scheduler with stuck state and a workflow dict for conflict keys."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        sched = AutonomousScheduler()
        wf_id = "stuck-workflow-456"
        wf = {
            "workflow_id": wf_id,
            "worktree_path": "/home/repo/.worktrees/stuck-456",
            "project_path": "/home/repo",
            "branch_name": "auto-dev/stuck-456",
            "batch_id": "batch-42",
            "current_phase": "development",
        }
        sched._in_progress_ids.add(wf_id)
        workspace, branch = sched._conflict_keys(wf)
        sched._in_progress_workspaces.add(workspace)
        sched._in_progress_branches.add(branch)
        sched._in_progress_batch_ids.add("batch-42")
        sched._running_orchestrators[wf_id] = MagicMock()
        return sched, wf_id, wf

    def test_clear_in_progress_removes_from_in_progress_ids(self, scheduler):
        """clear_in_progress must remove workflow_id from _in_progress_ids."""
        sched, wf_id = scheduler
        assert wf_id in sched._in_progress_ids

        with patch("app.routes.autonomous._get_repo") as mock_get_repo:
            mock_repo = MagicMock()
            mock_get_repo.return_value = mock_repo
            sched.clear_in_progress(wf_id)

        assert wf_id not in sched._in_progress_ids

    def test_clear_in_progress_removes_orphaned_orchestrator(self, scheduler):
        """clear_in_progress must remove orphaned orchestrator reference."""
        sched, wf_id = scheduler
        assert wf_id in sched._running_orchestrators

        with patch("app.routes.autonomous._get_repo"):
            sched.clear_in_progress(wf_id)

        assert wf_id not in sched._running_orchestrators

    def test_clear_in_progress_clears_conflict_keys_with_wf(self, scheduler_with_wf):
        """clear_in_progress must clear workspace/branch/batch keys when wf is provided."""
        sched, wf_id, wf = scheduler_with_wf
        workspace, branch = sched._conflict_keys(wf)
        assert workspace in sched._in_progress_workspaces
        assert branch in sched._in_progress_branches
        assert "batch-42" in sched._in_progress_batch_ids

        with patch("app.routes.autonomous._get_repo"):
            sched.clear_in_progress(wf_id, wf=wf)

        assert workspace not in sched._in_progress_workspaces
        assert branch not in sched._in_progress_branches
        assert "batch-42" not in sched._in_progress_batch_ids

    def test_clear_in_progress_skips_conflict_keys_without_wf(self, scheduler):
        """clear_in_progress without wf should still clear _in_progress_ids."""
        sched, wf_id = scheduler

        with patch("app.routes.autonomous._get_repo"):
            sched.clear_in_progress(wf_id)

        assert wf_id not in sched._in_progress_ids

    def test_clear_in_progress_releases_db_lock(self, scheduler):
        """clear_in_progress must force-release the DB lock regardless of owner."""
        sched, wf_id = scheduler

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_repo = MagicMock()
        mock_repo.db.get_connection.return_value = mock_conn

        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
            sched.clear_in_progress(wf_id)

        # Verify UPDATE was executed to clear locked_at / locked_by
        mock_cursor.execute.assert_called_once()
        sql_arg = mock_cursor.execute.call_args[0][0]
        assert "locked_at = NULL" in sql_arg
        assert "locked_by = NULL" in sql_arg
        mock_conn.commit.assert_called_once()

    def test_clear_in_progress_safe_on_missing_workflow(self):
        """clear_in_progress must not crash for a workflow_id not in any set."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        sched = AutonomousScheduler()
        # No workflow_id in any set — should be a no-op, not crash
        with patch("app.routes.autonomous._get_repo"):
            sched.clear_in_progress("nonexistent-wf")

    def test_clear_in_progress_continues_on_db_error(self, scheduler):
        """clear_in_progress must still clear in-memory state even if DB update fails."""
        sched, wf_id = scheduler

        mock_repo = MagicMock()
        mock_repo.db.get_connection.side_effect = Exception("DB connection lost")

        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
            sched.clear_in_progress(wf_id)

        # In-memory state should still be cleared even if DB lock release failed
        assert wf_id not in sched._in_progress_ids
        assert wf_id not in sched._running_orchestrators

    def test_retry_endpoint_calls_clear_in_progress(self):
        """retry_workflow must call clear_in_progress before updating DB."""
        from app.routes.autonomous import retry_workflow

        wf_id = "wf-retry-test"
        fake_user = {"id": 1, "role": "admin", "tenant_id": 1}

        with (
            patch("app.routes.autonomous._get_repo") as mock_get_repo,
            patch("app.services.autonomous_scheduler.AutonomousScheduler") as mock_sched_cls,
            patch("app.routes.autonomous._emit_event_safe"),
            patch("app.auth.decorators._extract_token", return_value="fake-token"),
            patch("app.auth.decorators._load_user_from_token", return_value=fake_user),
            patch("app.auth.decorators.enforce_password_change_requirement", return_value=None),
        ):

            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = {
                "workflow_id": wf_id,
                "status": "failed",
                "user_id": 1,
                "current_phase": "development",
                "retry_count": 3,
            }
            mock_get_repo.return_value = mock_repo

            mock_sched = MagicMock()
            mock_sched_cls.instance.return_value = mock_sched

            from flask import Flask

            app = Flask(__name__)
            with app.test_request_context():
                retry_workflow(wf_id)

            # Verify clear_in_progress was called with workflow dict
            mock_sched.clear_in_progress.assert_called_once()
            call_args = mock_sched.clear_in_progress.call_args
            assert call_args[0][0] == wf_id  # workflow_id positional
            assert call_args[1]["wf"]["workflow_id"] == wf_id  # wf kwarg
            # Verify DB was still updated
            mock_repo.update_workflow.assert_called_once()
