"""Worktree cleanup semantics (#2505 worktree_path retention).

The #23 node_modules shim that used to live here was REVERTED (#2694): it
executed via ``sudo -u <owner> bash -c`` which sudoers rejects by design
(#2650 root-RCE surface), so it never succeeded on multi-user prod and its
fail-soft milestone spammed every advance (5.2k rows in 4 days). Dependency
setup for frontend tests is now the agent's job — see the lazy dependency
preparation follow-up. These cleanup tests remain: they lock worktree_path
retention on failed cleanup (#2505), independent of the shim.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2505)]


def test_cleanup_failed_keeps_worktree_path_for_recreate(tmp_path):
    """A terminally-failed workflow's worktree DIR is removed but ``worktree_path``
    is KEPT so :meth:`ensure_worktree` recreates the worktree on retry/reset (#23).

    The cleanup docstring already promises "_ensure_worktree recreates it on the
    next cycle if retried/resumed", but clearing ``worktree_path`` here defeated
    that: ``ensure_worktree``'s empty-path guard then falls back to the main
    clone instead of recreating — so a reset failed workflow re-ran in the main
    clone and hit EACCES on ``node_modules/.vite-temp`` (c88afdc0/83ffb529/
    ee678c63). The completed/merged path still clears it (merged = done).
    """
    from unittest.mock import MagicMock, patch

    from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService

    orch = MagicMock()
    orch._workflow_id = "wid-failed-keep"
    orch.workflow = {
        "branch_name": "auto-dev/x",
        "worktree_path": str(tmp_path / "wt"),
        "project_path": str(tmp_path / "main"),
        "user_id": None,
    }
    updates: list[dict] = []
    orch._update_workflow.side_effect = lambda d: updates.append(d)

    fake_gh = MagicMock()
    fake_gh.has_uncommitted_changes.return_value = False  # clean → dir removed

    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=fake_gh):
        svc = GitWorkspaceService(orch)
        ok = svc.cleanup_worktree_and_branch(reason="failed", remove_worktree=True)

    assert ok is True
    fake_gh.remove_worktree.assert_called_once()
    # worktree_path must NOT be cleared (no update setting it to "")
    cleared = [u for u in updates if u.get("worktree_path") == ""]
    assert cleared == [], f"failed-cleanup must keep worktree_path for recreate, got {updates}"


def test_cleanup_completed_clears_worktree_path(tmp_path):
    """The completed/merged path still clears ``worktree_path`` (the merged
    workflow is done; ensure_worktree's empty-path guard correctly returns the
    main clone for any post-merge probe)."""
    from unittest.mock import MagicMock, patch

    from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService

    orch = MagicMock()
    orch._workflow_id = "wid-completed-clear"
    orch.workflow = {
        "branch_name": "auto-dev/y",
        "worktree_path": str(tmp_path / "wt"),
        "project_path": str(tmp_path / "main"),
        "user_id": None,
    }
    updates: list[dict] = []
    orch._update_workflow.side_effect = lambda d: updates.append(d)

    fake_gh = MagicMock()  # completed path: no dirty guard, reclaimed directly

    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=fake_gh):
        svc = GitWorkspaceService(orch)
        ok = svc.cleanup_worktree_and_branch(
            reason="completed", remove_worktree=True, remove_branch=True
        )

    assert ok is True
    # worktree_path IS cleared for completed
    cleared = [u for u in updates if u.get("worktree_path") == ""]
    assert cleared, f"completed-cleanup must clear worktree_path, got {updates}"
