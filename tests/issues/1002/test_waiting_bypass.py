"""Tests for waiting-workflow conflict-lock bypass.

Waiting workflows only execute ``_do_wait`` — a lightweight state transition
(DB update, no agent, no git). They must bypass batch/workspace/branch
conflict locks so they can resume (e.g. after cancel-with-feedback) even
while a batch sibling is actively running. Critically, the bypass must NOT
clobber another running workflow's locks in ``_advance_single``'s cleanup.
"""

from unittest.mock import MagicMock, patch

from app.services.autonomous_scheduler import AutonomousScheduler


def _scheduler() -> AutonomousScheduler:
    """A fresh, non-singleton scheduler instance for isolation."""
    return AutonomousScheduler()


# ── filtering: waiting workflows bypass all conflict locks ──────────────


class TestWaitingBypassFiltering:
    """Replicates the _process_workflows conflict filter to verify waiting
    workflows are not blocked by batch/workspace/branch locks."""

    def _would_block(self, sched: AutonomousScheduler, wf: dict) -> bool:
        """Replicate the git-conflict + batch filter in _process_workflows."""
        is_waiting = wf.get("status") == "waiting"
        batch_id = wf.get("batch_id")
        if batch_id and batch_id in sched._in_progress_batch_ids and not is_waiting:
            return True
        workspace, branch = sched._conflict_keys(wf)
        if workspace and workspace in sched._in_progress_workspaces and not is_waiting:
            return True
        if branch and branch in sched._in_progress_branches and not is_waiting:
            return True
        return False

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
        assert not self._would_block(sched, waiting_wf)

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
        assert not self._would_block(sched, waiting_wf)

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
        assert not self._would_block(sched, waiting_wf)

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
        assert self._would_block(sched, developing_wf)

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
        assert self._would_block(sched, developing_wf)

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
        assert self._would_block(sched, developing_wf)


# ── _advance_single: waiting workflows don't clobber locks ──────────────


class TestAdvanceSingleWaitingCleanup:
    """Verify _advance_single does not discard conflict keys for waiting
    workflows, preventing clobbering of a concurrently running sibling's
    locks."""

    def _run_advance_single(self, wf: dict, sched: AutonomousScheduler, lock_ok: bool = True):
        """Helper: run _advance_single with all external deps mocked."""
        repo = MagicMock()
        repo.get_workflow.return_value = wf
        repo.acquire_lock.return_value = lock_ok

        with (
            patch("app.routes.autonomous._get_repo", return_value=repo),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch("app.modules.governance.quota_manager.QuotaManager") as mock_qm,
        ):
            mock_orch_cls.return_value = MagicMock()
            mock_qm.return_value.check_quota.return_value = {"allowed": True, "reason": ""}
            sched._advance_single(wf["workflow_id"])
        return repo

    def test_waiting_does_not_discard_batch_id(self):
        """A waiting workflow's finally must not remove another workflow's
        batch_id from _in_progress_batch_ids."""
        sched = _scheduler()
        # Simulate a running sibling holding the batch lock
        sched._in_progress_batch_ids.add("batch-1")
        sched._in_progress_workspaces.add("/proj")
        sched._in_progress_branches.add("shared/branch")

        waiting_wf = {
            "workflow_id": "w-wait",
            "status": "waiting",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
            "user_id": 1,
        }

        self._run_advance_single(waiting_wf, sched)

        # The waiting workflow's cleanup must NOT have removed the sibling's keys
        assert "batch-1" in sched._in_progress_batch_ids
        assert "/proj" in sched._in_progress_workspaces
        assert "shared/branch" in sched._in_progress_branches
        # workflow_id IS removed (the advance completed)
        assert "w-wait" not in sched._in_progress_ids

    def test_developing_does_discard_batch_id(self):
        """A non-waiting workflow's finally DOES remove its own batch_id."""
        sched = _scheduler()
        sched._in_progress_batch_ids.add("batch-1")
        sched._in_progress_workspaces.add("/proj")
        sched._in_progress_branches.add("shared/branch")

        developing_wf = {
            "workflow_id": "w-dev",
            "status": "developing",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
            "user_id": 1,
        }

        self._run_advance_single(developing_wf, sched)

        # Non-waiting workflow's cleanup DOES remove its keys
        assert "batch-1" not in sched._in_progress_batch_ids
        assert "/proj" not in sched._in_progress_workspaces
        assert "shared/branch" not in sched._in_progress_branches
        assert "w-dev" not in sched._in_progress_ids

    def test_waiting_lock_failure_does_not_discard(self):
        """Even on lock-failure early return, a waiting workflow must not
        clobber another workflow's keys."""
        sched = _scheduler()
        sched._in_progress_batch_ids.add("batch-1")
        sched._in_progress_workspaces.add("/proj")
        sched._in_progress_branches.add("shared/branch")

        waiting_wf = {
            "workflow_id": "w-wait",
            "status": "waiting",
            "batch_id": "batch-1",
            "project_path": "/proj",
            "branch_name": "shared/branch",
            "user_id": 1,
        }

        self._run_advance_single(waiting_wf, sched, lock_ok=False)

        assert "batch-1" in sched._in_progress_batch_ids
        assert "/proj" in sched._in_progress_workspaces
        assert "shared/branch" in sched._in_progress_branches
        assert "w-wait" not in sched._in_progress_ids
