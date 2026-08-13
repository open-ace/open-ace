"""Regression: acceptance_verification must not serialize with same-repo
workflows via the project_path conflict-lock.

The verifier runs in an isolated ``git worktree add --detach`` checkout of the
merged commit — it neither mutates the project_path working tree nor checks
out the workflow's branch. Its persisted ``worktree_path`` is empty (the verify
worktree is created internally and not persisted), so the project_path fallback
in ``_conflict_keys`` otherwise keyed it on the shared repo. That held the
repo's conflict-lock for the verifier's full agent run (10-90 min) and starved
same-repo peers — workflow e274ec0e waited ~90 min behind cd939cbf's verifier
in the same repo. git worktrees are designed for concurrent use (isolated
index/HEAD, append-only object store), so a detached verify worktree does not
race with a same-repo peer's worktree.
"""

import threading
from unittest.mock import MagicMock

from app.services.autonomous_scheduler import AutonomousScheduler


def _verifier_wf(**overrides):
    base = {
        "workflow_id": "e274ec0e-bb68-46f6-b549-7a3b0936f3c0",
        "current_phase": "acceptance_verification",
        "status": "verification_pending",
        "project_path": "/srv/open-ace",  # same repo as a running merge workflow
        "worktree_path": "",  # persisted worktree_path is empty for verification
        "branch_name": "auto-dev/e274ec0e",
        "batch_id": "",
    }
    base.update(overrides)
    return base


def _scheduler(*, workspaces=None, branches=None, batches=None):
    """Bypass __init__ — only the conflict-lock sets are needed."""
    sched = AutonomousScheduler.__new__(AutonomousScheduler)
    sched._in_progress_workspaces = set(workspaces or ())
    sched._in_progress_branches = set(branches or ())
    sched._in_progress_batch_ids = set(batches or ())
    sched._in_progress_ids = set()
    sched._in_progress_lock = threading.Lock()
    return sched


def test_acceptance_verifier_conflict_key_is_unique_not_project_path():
    """_conflict_keys for acceptance_verification returns a per-workflow token
    (and no branch), never the shared project_path."""
    workspace, branch = AutonomousScheduler._conflict_keys(_verifier_wf())
    assert workspace != "/srv/open-ace"
    assert "e274ec0e" in workspace  # unique per workflow
    assert branch == ""  # detached checkout — no branch


def test_acceptance_verifier_not_blocked_by_same_repo_workflow():
    """A verifier must NOT be skipped because a same-repo merge workflow holds
    the project_path conflict-lock."""
    sched = _scheduler(
        workspaces={"/srv/open-ace"},  # held by the merge workflow
        branches={"auto-dev/c0758607"},
    )
    assert sched._workflow_blocked_by_conflict_locks(_verifier_wf()) is False


def test_acceptance_verifier_does_not_block_same_repo_workflow():
    """Conversely, a running verifier (unique token reserved) must not block a
    same-repo workflow that is itself keyed on project_path (new-branch /
    primary-fallback with no worktree). Before the fix the verifier reserved
    project_path and would have blocked this workflow."""
    sched = _scheduler(workspaces={"acceptance:e274ec0e-bb68-46f6-b549-7a3b0936f3c0"})
    same_repo_wf = {
        "workflow_id": "c0758607-0000-0000-0000-000000000000",
        "current_phase": "merge",
        "status": "merging",
        "project_path": "/srv/open-ace",
        "worktree_path": "",  # keyed on project_path (new-branch / primary-fallback)
        "branch_name": "auto-dev/c0758607",
    }
    assert sched._workflow_blocked_by_conflict_locks(same_repo_wf) is False


def test_non_acceptance_workflow_still_serializes_on_project_path():
    """The exemption is acceptance-only: a merge-phase workflow with no worktree
    still falls back to project_path and serializes on it (unchanged behavior)."""
    wf = {
        "workflow_id": "c0758607-0000-0000-0000-000000000000",
        "current_phase": "merge",
        "status": "merging",
        "project_path": "/srv/open-ace",
        "worktree_path": "",  # between cycles / primary-fallback → project_path
        "branch_name": "auto-dev/c0758607",
    }
    workspace, branch = AutonomousScheduler._conflict_keys(wf)
    assert workspace == "/srv/open-ace"  # project_path fallback still applies
    assert branch == "auto-dev/c0758607"
    sched = _scheduler(workspaces={"/srv/open-ace"})
    assert sched._workflow_blocked_by_conflict_locks(wf) is True
