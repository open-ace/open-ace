"""pr_review push check recovers worktree-on-main before failing (#2302, #28).

Defense-in-depth at the pr_review push: the central guard
(``_validate_repo_context_after_run``) recovers a worktree the agent left on
another branch after each agent run, but the worktree can still be on main at
push time (e.g. a failure→retry→reentry sequence — workflow 212 hit this).
``_ensure_branch_and_push`` tries ``host.recover_worktree_branch`` before
raising the permanent "Branch mismatch" failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.phases.pr_review import _ensure_branch_and_push

pytestmark = [pytest.mark.regression, pytest.mark.issue(2302)]


def _gh(current_branch: str, *, push_ok: bool = True):
    """Mock GitHubOps whose get_current_branch returns ``current_branch``."""
    gh = MagicMock()
    gh.get_current_branch.return_value = current_branch
    if not push_ok:
        gh.git_push.side_effect = RuntimeError("push failed")
    return gh


def _host(recover_return, *, recover_exc=None):
    """Mock PhaseHost whose recover_worktree_branch returns ``recover_return``."""
    host = MagicMock()
    host.workflow_id = "wf-test-12345678"
    if recover_exc is not None:
        host.recover_worktree_branch.side_effect = recover_exc
    else:
        host.recover_worktree_branch.return_value = recover_return
    return host


def test_push_when_already_on_feature_branch():
    """Worktree on the feature branch → no recovery, push proceeds."""
    gh = _gh("auto-dev/feature-branch")
    host = _host(recover_return=None)

    _ensure_branch_and_push(gh, host, "auto-dev/feature-branch", "feat-tip", "main-tip")

    host.recover_worktree_branch.assert_not_called()
    gh.git_push.assert_called_once_with(branch="auto-dev/feature-branch", force_with_lease=True)


def test_recovers_when_on_main_and_recovery_safe():
    """Worktree on main + recovery safe → recover, re-check, push proceeds."""
    # First get_current_branch → main (triggers recovery); after recovery the
    # re-check returns the feature branch.
    gh = _gh("main")
    gh.get_current_branch.side_effect = ["main", "auto-dev/feature-branch"]
    host = _host(recover_return="recovered worktree onto auto-dev/feature-branch")

    _ensure_branch_and_push(gh, host, "auto-dev/feature-branch", "feat-tip", "main-tip")

    host.recover_worktree_branch.assert_called_once_with(
        gh, "auto-dev/feature-branch", "feat-tip", "main-tip"
    )
    gh.git_push.assert_called_once_with(branch="auto-dev/feature-branch", force_with_lease=True)


def test_fails_when_on_main_and_recovery_unsafe():
    """Worktree on main + recovery NOT safe (None) → RuntimeError, no push."""
    gh = _gh("main")
    host = _host(recover_return=None)  # recovery refused (dirty/moved/advanced)

    with pytest.raises(RuntimeError, match="Branch mismatch before push"):
        _ensure_branch_and_push(gh, host, "auto-dev/feature-branch", "feat-tip", "main-tip")

    host.recover_worktree_branch.assert_called_once()
    gh.git_push.assert_not_called()


def test_recovery_exception_does_not_mask_mismatch():
    """If recover_worktree_branch itself raises, treat as 'no recovery' → fail closed."""
    gh = _gh("main")
    host = _host(recover_return=None, recover_exc=RuntimeError("checkout blew up"))

    with pytest.raises(RuntimeError, match="Branch mismatch before push"):
        _ensure_branch_and_push(gh, host, "auto-dev/feature-branch", "feat-tip", "main-tip")

    gh.git_push.assert_not_called()


def test_orchestrator_public_alias_delegates_to_private():
    """AutonomousOrchestrator.recover_worktree_branch (PhaseHost-facing) delegates
    to _recover_worktree_branch so the pr_review push can reach it via the host."""
    from unittest.mock import patch

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with patch.object(
        AutonomousOrchestrator, "_recover_worktree_branch", return_value="ok"
    ) as mock_private:
        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"),
        ):
            orch = AutonomousOrchestrator("wf-alias-test")
        gh = MagicMock()
        result = orch.recover_worktree_branch(gh, "auto-dev/x", "feat-tip", "main-tip")

    assert result == "ok"
    mock_private.assert_called_once_with(gh, "auto-dev/x", "feat-tip", "main-tip")
