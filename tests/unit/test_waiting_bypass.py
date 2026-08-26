"""Tests for waiting-workflow conflict-lock bypass (pure filter class).

The thread-bound companion (real heartbeat-thread start/join during
_advance_single cleanup) lives in
tests/integration/concurrency/test_waiting_advance_concurrent.py.

Waiting workflows only execute ``_do_wait`` — a lightweight state transition
(DB update, no agent, no git). They must bypass batch/workspace/branch
conflict locks so they can resume (e.g. after cancel-with-feedback) even
while a batch sibling is actively running. Critically, the bypass must NOT
clobber another running workflow's locks in ``_advance_single``'s cleanup.
"""

import pytest

from app.services.autonomous_scheduler import AutonomousScheduler

pytestmark = [pytest.mark.regression, pytest.mark.issue(1002)]


def _scheduler() -> AutonomousScheduler:
    """A fresh, non-singleton scheduler instance for isolation."""
    return AutonomousScheduler()


# ── filtering: waiting workflows bypass all conflict locks ──────────────


class TestWaitingBypassFiltering:
    """Verifies waiting workflows are not blocked by batch/workspace/branch
    conflict locks, by calling the real ``_workflow_blocked_by_conflict_locks``
    filter extracted from ``_process_workflows`` (PR #2016 review suggestion
    #2 — previously these tests re-implemented the filter locally, which would
    silently pass if the production filter drifted)."""

    def test_waiting_bypasses_batch_lock(self):
        sched = _scheduler()
        sched._in_progress_batch_ids.add("batch-1")
        waiting_wf = {
            "workflow_id": "w-wait",
            "status": "waiting",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
        }
        assert not sched._workflow_blocked_by_conflict_locks(waiting_wf)

    def test_waiting_bypasses_workspace_lock(self):
        sched = _scheduler()
        sched._in_progress_workspaces.add("/proj")
        waiting_wf = {
            "workflow_id": "w-wait",
            "status": "waiting",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
        }
        assert not sched._workflow_blocked_by_conflict_locks(waiting_wf)

    def test_waiting_bypasses_branch_lock(self):
        sched = _scheduler()
        sched._in_progress_branches.add("shared/branch")
        waiting_wf = {
            "workflow_id": "w-wait",
            "status": "waiting",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
        }
        assert not sched._workflow_blocked_by_conflict_locks(waiting_wf)

    def test_developing_still_blocked_by_batch_lock(self):
        sched = _scheduler()
        sched._in_progress_batch_ids.add("batch-1")
        developing_wf = {
            "workflow_id": "w-dev",
            "status": "developing",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
        }
        assert sched._workflow_blocked_by_conflict_locks(developing_wf)

    def test_developing_still_blocked_by_workspace_lock(self):
        sched = _scheduler()
        sched._in_progress_workspaces.add("/proj")
        developing_wf = {
            "workflow_id": "w-dev",
            "status": "developing",
            "batch_id": "",
            "project_path": "/proj",
            "branch_name": "auto-dev/x",
        }
        assert sched._workflow_blocked_by_conflict_locks(developing_wf)

    def test_developing_still_blocked_by_branch_lock(self):
        sched = _scheduler()
        sched._in_progress_branches.add("shared/branch")
        developing_wf = {
            "workflow_id": "w-dev",
            "status": "planning",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
        }
        assert sched._workflow_blocked_by_conflict_locks(developing_wf)


# ── _advance_single: waiting workflows don't clobber locks ──────────────
