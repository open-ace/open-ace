"""Tests for failed-workflow worktree/branch cleanup (Issue #1831, finding #1).

Covers the convergence point + cleanup extraction:

* ``_mark_failed`` is the single place a terminal failure is recorded, so the
  worktree dir is always reclaimed on terminal failure (closing the leak).
* ``_cleanup_worktree_and_branch`` defaults to ``keep_for_debug``: removes the
  worktree dir (recreatable via ``_ensure_worktree``) but keeps the git branch
  so an open PR is never orphaned (#1112).
* The #1112 timing dimension: transient (retryable) errors in ``advance`` keep
  the worktree for the next cycle; only terminal failures converge on
  ``_mark_failed`` and reclaim it.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1831",
        "user_id": 1,
        "status": "running",
        "current_phase": "development",
        "project_path": "/tmp/test-project",
        "branch_name": "auto-dev/test",
        "worktree_path": "/tmp/test-wt",
        "transient_retry_count": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _bare_orchestrator(wf):
    """Build an orchestrator bypassing __init__ (no DB/runner/emitter wiring)."""
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = wf["workflow_id"]
    orch.repo = MagicMock()
    orch.repo.get_workflow.return_value = wf
    orch.emitter = MagicMock()
    orch._gh = None
    return orch


def _update_calls(orch):
    """All dicts passed to repo.update_workflow."""
    return [call.args[1] for call in orch.repo.update_workflow.call_args_list]


class TestCleanupWorktreeAndBranch:
    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_removes_worktree_keeps_branch_by_default(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = False  # clean worktree

        ok = orch._cleanup_worktree_and_branch("failed")

        assert ok is True
        instance.remove_worktree.assert_called_once_with("/tmp/test-wt")
        instance.delete_branch.assert_not_called()  # keep_for_debug
        # worktree_path cleared, branch_name NOT cleared.
        updates = _update_calls(orch)
        assert {"worktree_path": ""} in updates
        assert not any("branch_name" in u for u in updates)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_removes_branch_when_requested(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = False  # clean worktree

        ok = orch._cleanup_worktree_and_branch(
            "completed", remove_worktree=True, remove_branch=True
        )

        assert ok is True
        instance.remove_worktree.assert_called_once()
        instance.delete_branch.assert_called_once_with("auto-dev/test")
        updates = _update_calls(orch)
        assert {"worktree_path": ""} in updates
        assert {"branch_name": ""} in updates

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_returns_false_and_swallows_githubops_error(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = False  # clean → reaches remove
        instance.remove_worktree.side_effect = GitHubOpsError("boom")

        # Must not raise; cleanup is best-effort.
        ok = orch._cleanup_worktree_and_branch("failed")
        assert ok is False
        # worktree_path NOT cleared because removal failed.
        updates = _update_calls(orch)
        assert {"worktree_path": ""} not in updates

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_noop_when_no_paths(self, mock_gh_cls):
        wf = _make_workflow(worktree_path="", branch_name="")
        orch = _bare_orchestrator(wf)

        ok = orch._cleanup_worktree_and_branch("failed")
        assert ok is True
        mock_gh_cls.assert_not_called()
        assert _update_calls(orch) == []


class TestMarkFailed:
    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_sets_failed_and_reclaims_worktree(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = False  # clean worktree

        orch._mark_failed("kaboom", phase="development")

        updates = _update_calls(orch)
        # First update records the terminal failure.
        assert updates[0] == {
            "status": "failed",
            "error_message": "kaboom",
            "transient_retry_count": 0,
        }
        # Worktree reclaimed...
        instance.remove_worktree.assert_called_once_with("/tmp/test-wt")
        # ...but branch kept (keep_for_debug).
        instance.delete_branch.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_mark_failed_emits_error(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        mock_gh_cls.return_value.has_uncommitted_changes.return_value = False

        orch._mark_failed("kaboom", phase="development")

        # An 'error' event is emitted with the phase + message.
        emitted = [c.args for c in orch.emitter.emit.call_args_list]
        assert ("wf-1831", "error", {"phase": "development", "error": "kaboom"}) in emitted
        orch.repo.create_event.assert_called()


class TestAdvanceTimingDimension:
    """#1112: transient errors keep the worktree; terminal failures reclaim it."""

    def _advance_with_phase_error(self, wf, exc):
        orch = _bare_orchestrator(wf)
        with (
            patch.object(orch, "_ensure_worktree"),
            patch.object(orch, "_do_development", side_effect=exc),
            patch.object(orch, "_mark_failed") as mock_mark_failed,
        ):
            orch.advance()
        return orch, mock_mark_failed

    def test_transient_error_keeps_worktree(self):
        """A transient (retryable) error must NOT converge on _mark_failed."""
        wf = _make_workflow(transient_retry_count=0)
        exc = GitHubOpsError("git push failed: Connection timed out")
        orch, mock_mark_failed = self._advance_with_phase_error(wf, exc)

        mock_mark_failed.assert_not_called()
        updates = _update_calls(orch)
        # Transient branch only bumps the retry counter; never sets failed or
        # clears worktree_path.
        assert any(u.get("transient_retry_count") == 1 for u in updates)
        assert not any(u.get("status") == "failed" for u in updates)
        assert not any(u.get("worktree_path") == "" for u in updates)

    def test_terminal_error_converges_on_mark_failed(self):
        """A non-transient error must converge on _mark_failed (worktree cleaned)."""
        wf = _make_workflow(transient_retry_count=0)
        exc = ValueError("genuine code bug")  # not transient
        orch, mock_mark_failed = self._advance_with_phase_error(wf, exc)

        mock_mark_failed.assert_called_once()
        # advance passed the phase through to _mark_failed.
        _args, kwargs = mock_mark_failed.call_args
        assert kwargs.get("phase") == "development"

    def test_transient_exhausted_converges_on_mark_failed(self):
        """Once transient retries exceed the cap, the next blip fails terminally."""
        wf = _make_workflow(transient_retry_count=6)  # at TRANSIENT_RETRY_MAX
        exc = GitHubOpsError("git push failed: Connection timed out")
        orch, mock_mark_failed = self._advance_with_phase_error(wf, exc)

        mock_mark_failed.assert_called_once()


class TestDirtyWorktreeGuard:
    """Review P1-b: a dirty worktree is retained for debug, never force-removed.

    ``git worktree remove --force`` discards uncommitted/untracked state that the
    branch (committed content only) cannot preserve and ``_ensure_worktree``
    cannot recreate on retry. Cleanup detects dirtiness and keeps the worktree +
    branch, recording why; only a clean worktree (committed state preserved) is
    reclaimed. Full reclamation of a retained worktree is left to the #2043
    reconciler / an operator.
    """

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_terminal_failure_does_not_force_remove_dirty_worktree(self, mock_gh_cls):
        wf = _make_workflow()
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = True  # dirty

        ok = orch._cleanup_worktree_and_branch("failed")

        assert ok is False  # nothing removed — intentional retention
        instance.remove_worktree.assert_not_called()
        instance.delete_branch.assert_not_called()
        updates = _update_calls(orch)
        # worktree_path is NOT cleared (worktree retained on disk for debug).
        assert not any(u.get("worktree_path") == "" for u in updates)
        # The reason is appended to error_message, naming the retained path.
        err_updates = [u["error_message"] for u in updates if "error_message" in u]
        assert err_updates
        assert "/tmp/test-wt" in err_updates[-1]
        assert "uncommitted" in err_updates[-1]

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_pre_pr_push_failure_can_cleanup_after_commit_is_preserved(self, mock_gh_cls):
        """A clean worktree (state already committed) is safe to reclaim.

        Models the original #1831 scenario — a pre-PR push hard-fail AFTER the
        agent committed: nothing uncommitted would be lost, so the worktree dir
        can be reclaimed (the branch still keeps the committed work).
        """
        wf = _make_workflow(current_phase="push")
        orch = _bare_orchestrator(wf)
        instance = mock_gh_cls.return_value
        instance.has_uncommitted_changes.return_value = False  # clean (committed)

        ok = orch._cleanup_worktree_and_branch("failed")

        assert ok is True
        instance.remove_worktree.assert_called_once_with("/tmp/test-wt")
        updates = _update_calls(orch)
        assert {"worktree_path": ""} in updates
