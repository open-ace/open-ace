"""merge conflict resolution must free the branch when the workflow is in the
primary-repo fallback (#2307).

``resolve_merge_conflicts`` creates a temp worktree for the PR branch so the
agent can resolve conflicts in isolation. Git forbids the same branch in two
worktrees, so the method first removes the workflow's dedicated worktree to
free the branch. But ``_get_gh`` (orchestrator.py:1043) explicitly supports a
**primary-repo fallback** — when ``worktree_path`` is empty (the dedicated
worktree was lost, e.g. after a prior failure), the workflow operates directly
in ``project_path`` with the feature branch checked out there. In that state
there is no dedicated worktree to remove, the branch stays locked in primary,
and ``git worktree add .worktrees/merge-* <branch>`` fails with exit 128
("branch already used by '<project_path>'"). Prod workflow 212 (PR #2292) hit
this and stuck in ``failed``.

The fix detaches primary's HEAD (``git checkout --detach``) to release the
branch WITHOUT switching primary to a possibly-stale main or touching the
branch ref — never modify primary's working state beyond this release. These
tests lock that contract.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

PROJECT_PATH = "/srv/repo"
BRANCH = "auto-dev/wf2307"
PR_NUMBER = 2292


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-2307abcd",
        "title": "merge primary-fallback (#2307)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": BRANCH,
        # Primary-fallback: dedicated worktree was lost, workflow runs in primary.
        "worktree_path": "",
        "preferred_worktree_path": "/srv/repo/.worktrees/wf-2307abcd",
        "project_path": PROJECT_PATH,
        "workspace_type": "local",
        "current_phase": "merge",
        "status": "merging",
        "github_pr_number": PR_NUMBER,
        "github_issue_number": 2307,
        "dev_round": 1,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf["workflow_id"],
        }
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    o._update_workflow = MagicMock()
    o._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    o._accumulate_tokens = MagicMock()
    o._write_phase_usage = MagicMock()
    o._validate_autonomous_change_scope = MagicMock(return_value="")
    o._ancestor_check = MagicMock(return_value=True)
    o._sync_worktree_to_pr_remote_head = MagicMock()
    return o


def _temp_path(wf):
    import os

    return os.path.normpath(
        os.path.join(wf["project_path"], ".worktrees", f"merge-{wf['workflow_id'][:8]}")
    )


def _run_git_dispatch(*, merge_failures=True):
    """A _run_git side_effect that succeeds for detach/fetch/rev-parse and fails
    the merge (non-conflict) so the method raises after the worktree dance —
    letting us assert the detach + add_worktree happened.
    """

    def _dispatch(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        cmd = list(cmd or [])
        head = cmd[0] if cmd else ""
        if head == "checkout" and len(cmd) > 1 and cmd[1] == "--detach":
            return MagicMock(returncode=0, stdout="", stderr="")
        if head == "fetch":
            return MagicMock(returncode=0, stdout="", stderr="")
        if head == "merge":
            # Non-conflict failure → GitHubOpsError raised by the caller code.
            return MagicMock(returncode=1, stdout="", stderr="merge boom")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _dispatch


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_detaches_to_free_branch_then_creates_temp(mock_gh_cls):
    """#2307: worktree_path empty + primary holds the feature branch → detach
    primary HEAD, then the temp worktree is created (no exit-128)."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh

    mock_gh.get_current_branch.return_value = BRANCH  # primary holds feature branch
    mock_gh.has_uncommitted_changes.return_value = False
    mock_gh.get_current_commit.return_value = "feat-tip"
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError, match="(?i)merge"):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # The branch was freed by detaching primary HEAD — NOT by checkout main.
    detach_calls = [
        c
        for c in mock_gh._run_git.call_args_list
        if c.args and c.args[0][:2] == ["checkout", "--detach"]
    ]
    assert detach_calls, "expected primary HEAD to be detached to free the branch"
    # The temp merge worktree WAS created (the bug was that this never happened).
    assert any(
        c.args[:1] == (_temp_path(wf),) for c in mock_gh.add_worktree.call_args_list if c.args
    )


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_detaches_not_checkout_main(mock_gh_cls):
    """#2307: the fix must use ``checkout --detach`` (no ref change, no stale
    main), never ``checkout main`` (which would violate the #822/#2041 'never
    touch primary' invariant)."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    mock_gh.get_current_branch.return_value = BRANCH
    mock_gh.has_uncommitted_changes.return_value = False
    mock_gh.get_current_commit.return_value = "feat-tip"
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    checkout_cmds = [
        list(c.args[0])
        for c in mock_gh._run_git.call_args_list
        if c.args and c.args[0] and c.args[0][0] == "checkout"
    ]
    assert any(cmd[1:2] == ["--detach"] for cmd in checkout_cmds)
    assert not any(
        cmd[1:2] == ["main"] for cmd in checkout_cmds
    ), "must not `checkout main` (violates never-touch-primary); use --detach"


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_fails_closed_on_uncommitted_changes(mock_gh_cls):
    """#2307: if primary has uncommitted changes, fail closed — never detach
    over a dirty tree (would risk losing agent work) and never create the temp."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    mock_gh.get_current_branch.return_value = BRANCH
    mock_gh.has_uncommitted_changes.return_value = True  # dirty primary
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError, match="(?i)uncommitted"):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # No detach, no temp worktree.
    assert not any(
        c.args and c.args[0][:2] == ["checkout", "--detach"]
        for c in mock_gh._run_git.call_args_list
    )
    mock_gh.add_worktree.assert_not_called()


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_skips_detach_when_branch_already_free(mock_gh_cls):
    """#2307: if primary is NOT on the feature branch (already detached, or on
    main from a prior cycle), the branch is already free — no detach needed,
    the temp worktree is created directly. Covers CI-repair re-entry."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    # Primary already on main (e.g. left there by a prior resolve round).
    mock_gh.get_current_branch.return_value = "main"
    mock_gh.get_current_commit.return_value = "feat-tip"
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError, match="(?i)merge"):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # Branch was already free → no detach.
    assert not any(
        c.args and c.args[0][:2] == ["checkout", "--detach"]
        for c in mock_gh._run_git.call_args_list
    )
    # Temp worktree was still created.
    assert any(
        c.args[:1] == (_temp_path(wf),) for c in mock_gh.add_worktree.call_args_list if c.args
    )


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_detached_head_skips_detach(mock_gh_cls):
    """#2307: re-entry after a prior detach — primary is in detached HEAD
    (``get_current_branch`` returns "" because ``git branch --show-current``
    prints nothing on a detached HEAD), so the branch is already free. No
    second detach; the temp worktree is created directly."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    # Primary in detached HEAD — the real re-entry state after a prior detach.
    mock_gh.get_current_branch.return_value = ""
    mock_gh.get_current_commit.return_value = "feat-tip"
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError, match="(?i)merge"):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # Already detached → no second detach.
    assert not any(
        c.args and c.args[0][:2] == ["checkout", "--detach"]
        for c in mock_gh._run_git.call_args_list
    )
    assert any(
        c.args[:1] == (_temp_path(wf),) for c in mock_gh.add_worktree.call_args_list if c.args
    )


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_primary_fallback_add_worktree_failure_is_reenterable(mock_gh_cls):
    """#2307: detach succeeded but the temp worktree creation then fails → the
    error propagates (fail-closed) and primary stays detached, so the next
    re-entry finds ``get_current_branch() == ""`` and retries add_worktree
    without re-detaching. No stuck state."""
    wf = _make_workflow()
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    mock_gh.get_current_branch.return_value = BRANCH
    mock_gh.has_uncommitted_changes.return_value = False
    mock_gh.get_current_commit.return_value = "feat-tip"
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(side_effect=_run_git_dispatch())
    # Detach succeeds, but the temp worktree creation then fails.
    mock_gh.add_worktree.side_effect = GitHubOpsError("worktree add failed")
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError, match="worktree add failed"):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # Primary WAS detached to free the branch (the branch-freeing ran)...
    assert any(
        c.args and c.args[0][:2] == ["checkout", "--detach"]
        for c in mock_gh._run_git.call_args_list
    )
    # ...and the temp worktree creation was attempted before the failure.
    mock_gh.add_worktree.assert_called_once()


@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_normal_worktree_path_path_unaffected(mock_gh_cls):
    """#2307 regression guard: the dedicated-worktree path (worktree_path set)
    must behave exactly as before — remove original, create temp, no detach of
    primary (primary was never holding the branch in this mode)."""
    wt_path = "/srv/repo/.worktrees/wf-2307abcd"
    wf = _make_workflow(worktree_path=wt_path)
    o = _make_orchestrator(wf)
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh

    def remove(path):
        if path == wt_path:
            return {"removed": path}
        raise GitHubOpsError("temp gone")

    mock_gh.remove_worktree.side_effect = remove
    mock_gh.add_worktree.return_value = {"worktree_path": wt_path, "branch": BRANCH}
    mock_gh.path_exists_as_user.return_value = False
    mock_gh.list_worktrees.return_value = [{"path": wt_path, "branch": BRANCH}]
    mock_gh.resolve_commit.return_value = "main-head"
    mock_gh._run_git = MagicMock(
        return_value=MagicMock(returncode=1, stdout="", stderr="merge boom")
    )
    o._gh = mock_gh

    with pytest.raises(GitHubOpsError):
        o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

    # Primary was never detached in the dedicated-worktree path.
    assert not any(
        c.args and len(c.args[0]) >= 2 and c.args[0][:2] == ["checkout", "--detach"]
        for c in mock_gh._run_git.call_args_list
    )
    # Original worktree was removed (the normal transition).
    assert any(c.args[:1] == (wt_path,) for c in mock_gh.remove_worktree.call_args_list if c.args)
