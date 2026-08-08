"""Issue #2431: the parked state must be visible to the sweeps that maintain it.

`verification_pending` was excluded from every maintenance query, so for the
whole parked window the workflow was invisible to Git cleanup retry (#2043) and
to the startup orphan-PID sweep.

Widening the cleanup query introduces a race that did not exist before: it runs
on the scheduler thread every tick, takes neither the in-progress set nor the DB
lock, and `_perform_git_cleanup` removes the worktree AND the branch. That was
safe only while the query returned `status='completed'` rows, which are never
advanced. So the guard is part of the same change, not a follow-up.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.services.autonomous_scheduler import (
    AutonomousScheduler,
    _is_in_flight,
    _retry_pending_git_cleanups,
)


class TestMaintenanceQueriesSeeParkedWorkflows:
    def test_cleanup_query_includes_verification_pending(self):
        db = MagicMock()
        db.fetch_all.return_value = []
        AutonomousWorkflowRepository(db).get_workflows_pending_cleanup()
        sql = db.fetch_all.call_args[0][0]
        assert "verification_pending" in sql, (
            "a workflow parked at acceptance_verification has a merged PR and a "
            "due cleanup; excluding it leaves the retry sweep blind"
        )
        assert "cleanup_status = 'pending'" in sql

    def test_active_pid_query_includes_verification_pending(self):
        db = MagicMock()
        conn = MagicMock()
        db.get_connection.return_value = conn
        conn.cursor.return_value.fetchall.return_value = []
        AutonomousWorkflowRepository(db).get_workflows_with_active_pid()
        params = conn.cursor.return_value.execute.call_args[0][1]
        assert "verification_pending" in params


class TestCleanupSweepDoesNotRaceAnAdvance:
    def _run_sweep(self, in_progress: set[str]) -> MagicMock:
        repo = MagicMock()
        repo.get_workflows_pending_cleanup.return_value = [
            {"workflow_id": "wf-parked", "cleanup_next_retry_at": ""}
        ]
        scheduler = AutonomousScheduler.__new__(AutonomousScheduler)
        scheduler._in_progress_ids = in_progress
        scheduler._in_progress_lock = threading.Lock()
        orchestrator = MagicMock()
        with (
            patch.object(AutonomousScheduler, "_instance", scheduler),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator",
                return_value=orchestrator,
            ),
        ):
            _retry_pending_git_cleanups(repo)
        return orchestrator

    def test_in_flight_workflow_is_not_swept(self):
        """Deleting the worktree under a running verifier is the failure mode."""
        orchestrator = self._run_sweep({"wf-parked"})
        orchestrator._perform_git_cleanup.assert_not_called()

    def test_idle_workflow_is_still_swept(self):
        """The guard must not disable the sweep it is protecting."""
        orchestrator = self._run_sweep(set())
        orchestrator._perform_git_cleanup.assert_called_once()

    def test_no_scheduler_instance_does_not_block_the_startup_sweep(self):
        """The startup sweep runs before the singleton exists."""
        with patch.object(AutonomousScheduler, "_instance", None):
            assert _is_in_flight("anything") is False
