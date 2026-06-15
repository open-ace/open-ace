"""Tests for autonomous scheduler git-conflict dedup + paused-slot reclaim (#1002).

Covers:
  - ``_conflict_keys``: worktree_path wins over project_path; empty worktree
    falls back to project_path (new-branch/same-branch share the main dir);
    branch from branch_name.
  - Dedup allows a fork (separate worktree) to run parallel to its parent
    (same project_path, different worktree), while still serializing batch
    workflows that share a branch and new-branch workflows that share the
    main project dir.
  - ``_reclaim_paused_slots``: a workflow paused mid-advance releases its
    git-conflict keys (so a fork sharing project_path isn't starved) but
    KEEPS its workflow_id (preventing a double-advance race on resume).
"""

from unittest.mock import MagicMock

from app.services.autonomous_scheduler import AutonomousScheduler


def _scheduler() -> AutonomousScheduler:
    """A fresh, non-singleton scheduler instance for isolation."""
    return AutonomousScheduler()


# ── _conflict_keys ───────────────────────────────────────────────────────


class TestConflictKeys:
    def test_worktree_takes_precedence_over_project_path(self):
        wf = {"worktree_path": "/wt/fork-abc", "project_path": "/proj", "branch_name": "fork/abc"}
        assert AutonomousScheduler._conflict_keys(wf) == ("/wt/fork-abc", "fork/abc")

    def test_falls_back_to_project_path_when_no_worktree(self):
        # new-branch / same-branch: operates in the main repo dir
        wf = {"worktree_path": "", "project_path": "/proj", "branch_name": "auto-dev/parent"}
        assert AutonomousScheduler._conflict_keys(wf) == ("/proj", "auto-dev/parent")

    def test_project_path_fallback_when_worktree_none(self):
        wf = {"project_path": "/proj", "branch_name": "auto-dev/x"}
        assert AutonomousScheduler._conflict_keys(wf)[0] == "/proj"

    def test_empty_when_nothing_set(self):
        assert AutonomousScheduler._conflict_keys({}) == ("", "")

    def test_empty_branch_when_unset(self):
        wf = {"project_path": "/proj"}
        workspace, branch = AutonomousScheduler._conflict_keys(wf)
        assert workspace == "/proj"
        assert branch == ""


# ── dedup conflict predicate (replicates the _process_workflows filter) ──


def _would_block(sched: AutonomousScheduler, wf: dict) -> bool:
    """Replicate the git-conflict filter in _process_workflows."""
    workspace, branch = sched._conflict_keys(wf)
    return (bool(workspace) and workspace in sched._in_progress_workspaces) or (
        bool(branch) and branch in sched._in_progress_branches
    )


class TestDedup:
    def test_fork_runs_parallel_with_parent(self):
        # Parent: new-branch in main repo dir. Fork: its own worktree.
        sched = _scheduler()
        parent = {"worktree_path": "", "project_path": "/proj", "branch_name": "auto-dev/parent"}
        fork = {
            "worktree_path": "/proj/../fork-from-abc",
            "project_path": "/proj",
            "branch_name": "fork/abc",
        }
        # Parent is in-progress
        ws, br = sched._conflict_keys(parent)
        sched._in_progress_workspaces.add(ws)
        sched._in_progress_branches.add(br)
        # Fork has a different worktree AND a different branch -> not blocked
        assert not _would_block(sched, fork)

    def test_new_branch_sharing_main_dir_serializes(self):
        # Two new-branch workflows in the same project dir conflict on workspace.
        sched = _scheduler()
        first = {"worktree_path": "", "project_path": "/proj", "branch_name": "auto-dev/a"}
        second = {"worktree_path": "", "project_path": "/proj", "branch_name": "auto-dev/b"}
        ws, br = sched._conflict_keys(first)
        sched._in_progress_workspaces.add(ws)
        sched._in_progress_branches.add(br)
        assert _would_block(sched, second)  # same workspace (/proj)

    def test_batch_sharing_branch_serializes(self):
        # Batch workflows assigned the same branch conflict even with distinct
        # worktrees (git forbids the same branch in two worktrees).
        sched = _scheduler()
        first = {"worktree_path": "/wt/a", "project_path": "/proj", "branch_name": "shared/branch"}
        second = {"worktree_path": "/wt/b", "project_path": "/proj", "branch_name": "shared/branch"}
        ws, br = sched._conflict_keys(first)
        sched._in_progress_workspaces.add(ws)
        sched._in_progress_branches.add(br)
        # different worktree, but SAME branch -> blocked
        assert _would_block(sched, second)

    def test_distinct_worktrees_and_branches_both_free(self):
        sched = _scheduler()
        sched._in_progress_workspaces.add("/wt/a")
        sched._in_progress_branches.add("branch/a")
        other = {"worktree_path": "/wt/b", "project_path": "/proj", "branch_name": "branch/b"}
        assert not _would_block(sched, other)


# ── _reclaim_paused_slots ────────────────────────────────────────────────


class TestReclaimPausedSlots:
    def _parent_wf(self):
        return {
            "workflow_id": "parent-1234",
            "status": "paused",
            "worktree_path": "",  # new-branch: main repo dir
            "project_path": "/proj",
            "branch_name": "auto-dev/parent",
            "batch_id": "batch-1",
        }

    def test_releases_conflict_keys_but_keeps_workflow_id(self):
        sched = _scheduler()
        parent = self._parent_wf()
        ws, br = sched._conflict_keys(parent)
        sched._in_progress_ids.add(parent["workflow_id"])
        sched._in_progress_workspaces.add(ws)
        sched._in_progress_branches.add(br)
        sched._in_progress_batch_ids.add(parent["batch_id"])

        repo = MagicMock()
        repo.get_workflow.return_value = parent

        sched._reclaim_paused_slots(repo)

        # git-conflict keys released -> a fork sharing /proj can now run
        assert ws not in sched._in_progress_workspaces
        assert br not in sched._in_progress_branches
        assert parent["batch_id"] not in sched._in_progress_batch_ids
        # workflow_id KEPT -> no double-advance race when the frozen agent resumes
        assert parent["workflow_id"] in sched._in_progress_ids

    def test_keeps_active_workflow_keys(self):
        sched = _scheduler()
        parent = self._parent_wf()
        active = {
            "workflow_id": "child-5678",
            "status": "developing",
            "worktree_path": "/wt/fork",
            "project_path": "/proj",
            "branch_name": "fork/abc",
            "batch_id": None,
        }
        for w in (parent, active):
            ws, br = sched._conflict_keys(w)
            sched._in_progress_ids.add(w["workflow_id"])
            sched._in_progress_workspaces.add(ws)
            sched._in_progress_branches.add(br)

        def get_wf(wid):
            return parent if wid == parent["workflow_id"] else active

        repo = MagicMock()
        repo.get_workflow.side_effect = get_wf

        sched._reclaim_paused_slots(repo)

        # parent (paused) keys released; active child keys untouched
        assert sched._conflict_keys(parent)[0] not in sched._in_progress_workspaces
        assert sched._conflict_keys(active)[0] in sched._in_progress_workspaces
        assert active["workflow_id"] in sched._in_progress_ids

    def test_noop_when_none_paused(self):
        sched = _scheduler()
        active = {
            "workflow_id": "w1",
            "status": "developing",
            "worktree_path": "/wt",
            "project_path": "/proj",
            "branch_name": "b1",
            "batch_id": None,
        }
        ws, br = sched._conflict_keys(active)
        sched._in_progress_ids.add(active["workflow_id"])
        sched._in_progress_workspaces.add(ws)
        sched._in_progress_branches.add(br)

        repo = MagicMock()
        repo.get_workflow.return_value = active
        sched._reclaim_paused_slots(repo)

        assert ws in sched._in_progress_workspaces  # unchanged
        assert active["workflow_id"] in sched._in_progress_ids

    def test_noop_when_nothing_in_progress(self):
        sched = _scheduler()
        repo = MagicMock()
        sched._reclaim_paused_slots(repo)  # must not call repo / must not raise
        repo.get_workflow.assert_not_called()
