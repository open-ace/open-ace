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

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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
    def _run_sweep(
        self, in_progress: set[str], *, db_lock_available: bool = True
    ) -> tuple[MagicMock, MagicMock]:
        repo = MagicMock()
        repo.acquire_cleanup_lock.return_value = db_lock_available
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
        return repo, orchestrator

    def test_in_flight_workflow_is_not_swept(self):
        """Deleting the worktree under a running verifier is the failure mode."""
        repo, orchestrator = self._run_sweep({"wf-parked"})
        orchestrator._perform_git_cleanup.assert_not_called()
        repo.acquire_cleanup_lock.assert_not_called()

    def test_idle_workflow_is_still_swept(self):
        """The guard must not disable the sweep it is protecting."""
        repo, orchestrator = self._run_sweep(set())
        orchestrator._perform_git_cleanup.assert_called_once()
        repo.acquire_cleanup_lock.assert_called_once()
        repo.release_lock.assert_called_once()

    def test_other_scheduler_db_lock_blocks_cleanup(self):
        """A fresh lock in another process must protect its live verifier.

        A rolling restart creates a new scheduler whose singleton starts empty;
        only the distributed workflow lock crosses that process boundary.
        """
        repo, orchestrator = self._run_sweep(set(), db_lock_available=False)
        orchestrator._perform_git_cleanup.assert_not_called()
        repo.acquire_cleanup_lock.assert_called_once()
        repo.release_lock.assert_not_called()

    def test_no_scheduler_instance_does_not_block_the_startup_sweep(self):
        """The startup sweep runs before the singleton exists."""
        with patch.object(AutonomousScheduler, "_instance", None):
            assert _is_in_flight("anything") is False

    def test_manual_acceptance_pause_keeps_conflict_slots(self):
        """SIGCONT-able verifier must keep batch/workspace/branch reservations."""
        scheduler = AutonomousScheduler.__new__(AutonomousScheduler)
        scheduler._in_progress_ids = {"wf-parked"}
        scheduler._in_progress_batch_ids = {"batch-1"}
        scheduler._in_progress_workspaces = {"/tmp/verify"}
        scheduler._in_progress_branches = {"verify-branch"}
        scheduler._in_progress_lock = threading.Lock()
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-parked",
            "status": "paused",
            "current_phase": "acceptance_verification",
            # A prior terminal verdict can remain while an edited issue causes
            # a new verifier run. in_progress/agent_pid remain authoritative.
            "verification_status": "indeterminate",
            "agent_pid": 4242,
            "batch_id": "batch-1",
            "worktree_path": "/tmp/verify",
            "branch_name": "verify-branch",
        }

        scheduler._reclaim_paused_slots(repo)

        assert "wf-parked" in scheduler._in_progress_ids
        assert "batch-1" in scheduler._in_progress_batch_ids
        assert "/tmp/verify" in scheduler._in_progress_workspaces
        assert "verify-branch" in scheduler._in_progress_branches


class _SQLiteDB:
    def __init__(self, path: str):
        self.path = path

    def get_connection(self):
        return sqlite3.connect(self.path)


def test_cleanup_lock_does_not_break_stale_live_agent_lease(tmp_path):
    """A 60-minute verifier remains protected past the generic 30-minute TTL."""
    db_path = str(tmp_path / "cleanup-lock.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE autonomous_workflows (
            workflow_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            locked_at TEXT,
            locked_by TEXT,
            agent_pid INTEGER
        )""")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO autonomous_workflows VALUES (?, ?, ?, ?, ?)",
        [
            ("live", "verification_pending", stale, "old-worker", 4242),
            # PID has already cleared, but the same verification advance can
            # still be running mechanical gates for the remainder of its hour.
            ("post-agent", "verification_pending", stale, "old-worker", None),
            ("orphan", "completed", stale, "dead-worker", None),
        ],
    )
    conn.commit()
    conn.close()

    repo = AutonomousWorkflowRepository(_SQLiteDB(db_path))
    with patch("app.repositories.database.adapt_sql", side_effect=lambda sql: sql):
        assert repo.acquire_cleanup_lock("live", "cleanup") is False
        assert repo.acquire_cleanup_lock("post-agent", "cleanup") is False
        assert repo.acquire_cleanup_lock("orphan", "cleanup") is True

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT workflow_id, locked_by FROM autonomous_workflows"))
    conn.close()
    assert rows == {
        "live": "old-worker",
        "post-agent": "old-worker",
        "orphan": "cleanup",
    }
