"""Worktree transition exception-safety for merge-conflict resolution (#2041).

``_resolve_merge_conflicts`` temporarily removes the workflow's worktree, creates
a temp worktree for the PR branch, resolves, pushes, then restores the original.
The DB / git-worktree registry / disk are not updated atomically, so failures
at any step could leave the workflow pointing at the main checkout (HEAD=main).

These lock in the #2041 in-process guarantees (the SIGKILL-recovery acceptance
criterion is tracked separately). Helpers mirror tests/issues/822/.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

WT_PATH = "/srv/repo/.worktrees/wf-2041"
PROJECT_PATH = "/srv/repo"
BRANCH = "auto-dev/wf2041"
PR_NUMBER = 2041


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-2041",
        "title": "worktree transition safety (#2041)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": BRANCH,
        "worktree_path": WT_PATH,
        "project_path": PROJECT_PATH,
        "workspace_type": "local",
        "current_phase": "merge",
        "status": "merging",
        "github_pr_number": PR_NUMBER,
        "github_issue_number": 2041,
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
    return o, mock_repo


def _db_updates(o):
    """All dict args passed to _update_workflow."""
    return [
        c.args[0]
        for c in o._update_workflow.call_args_list
        if c.args and isinstance(c.args[0], dict)
    ]


def _temp_path(wf):
    import os

    return os.path.normpath(f"{wf['project_path']}/../merge-{wf['workflow_id'][:8]}")


class TestWorktreeTransitionSafety:
    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_original_worktree_remove_failure_keeps_db_and_aborts_temp_creation(self, mock_gh_cls):
        """#2041 #1: if removing the original worktree fails (and it's still
        registered), the DB must NOT be cleared and the temp worktree must NOT
        be created."""
        wf = _make_workflow()
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        mock_gh.remove_worktree.side_effect = GitHubOpsError("remove failed")
        mock_gh.list_worktrees.return_value = [{"path": WT_PATH, "branch": BRANCH}]
        mock_gh._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        o._gh = mock_gh

        with pytest.raises(GitHubOpsError):
            o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

        # DB worktree_path was NOT cleared.
        assert not any(u.get("worktree_path") == "" for u in _db_updates(o))
        # No temp worktree was created.
        assert not any(
            c.args and "merge-" in str(c.args[0]) for c in mock_gh.add_worktree.call_args_list
        )

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_temp_worktree_creation_failure_restores_original_worktree(self, mock_gh_cls):
        """#2041 #2: original removed OK, but temp worktree creation fails → the
        original worktree must be restored (temp creation now lives inside the
        protective try/finally)."""
        wf = _make_workflow()
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        temp = _temp_path(wf)

        def remove(path):
            if path == WT_PATH:
                return {"removed": path}
            raise GitHubOpsError("temp already gone")  # finally temp-cleanup may fail

        def add(path, branch):
            if path == temp:
                raise GitHubOpsError("temp create failed")
            return {"worktree_path": path, "branch": branch}

        mock_gh.remove_worktree.side_effect = remove
        mock_gh.add_worktree.side_effect = add
        mock_gh.path_exists_as_user.return_value = False  # .git missing → re-add on restore
        mock_gh.list_worktrees.return_value = [{"path": WT_PATH, "branch": BRANCH}]
        mock_gh._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        o._gh = mock_gh

        with pytest.raises(GitHubOpsError):
            o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

        # Original worktree was restored.
        assert any(
            c.args[:2] == (WT_PATH, BRANCH) for c in mock_gh.add_worktree.call_args_list if c.args
        )
        assert any(u.get("worktree_path") == WT_PATH for u in _db_updates(o))

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_resolution_failure_restores_original_worktree(self, mock_gh_cls):
        """#2041 #3: a failure during merge/resolve/push must still tear down the
        temp worktree and restore the original."""
        wf = _make_workflow()
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        def remove(path):
            if path == WT_PATH:
                return {"removed": path}
            raise GitHubOpsError("temp gone")

        mock_gh.remove_worktree.side_effect = remove
        mock_gh.add_worktree.return_value = {"worktree_path": WT_PATH, "branch": BRANCH}
        mock_gh.path_exists_as_user.return_value = False
        mock_gh.list_worktrees.return_value = [{"path": WT_PATH, "branch": BRANCH}]
        # Non-conflict merge failure → raises inside the try.
        mock_gh._run_git = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="boom"))
        mock_gh.resolve_commit.return_value = "main-head"
        o._gh = mock_gh

        with pytest.raises(GitHubOpsError):
            o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

        assert any(u.get("worktree_path") == WT_PATH for u in _db_updates(o))
        assert any(
            c.args[:2] == (WT_PATH, BRANCH) for c in mock_gh.add_worktree.call_args_list if c.args
        )

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_temp_cleanup_failure_does_not_report_false_success(self, mock_gh_cls):
        """#2041 #4: temp worktree removal failure is non-fatal but must not
        skip the original restore or be silently swallowed as success."""
        wf = _make_workflow()
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        def remove(path):
            # Both removals fail — original (top) and temp (finally).
            raise GitHubOpsError("remove failed")

        # For remove failure of the ORIGINAL, list_worktrees must show it's
        # gone so the idempotent helper treats it as removed (otherwise it
        # re-raises and we never reach the merge). The post-restore verify then
        # reads it again and must find the restored worktree registered.
        mock_gh.list_worktrees.side_effect = [
            [],
            [{"path": WT_PATH, "branch": BRANCH}],
        ]
        mock_gh.remove_worktree.side_effect = remove
        mock_gh.add_worktree.return_value = {"worktree_path": WT_PATH, "branch": BRANCH}
        mock_gh.path_exists_as_user.return_value = False
        mock_gh._run_git = MagicMock(
            return_value=MagicMock(returncode=1, stdout="", stderr="merge boom")
        )
        mock_gh.resolve_commit.return_value = "main-head"
        o._gh = mock_gh

        with pytest.raises(GitHubOpsError):  # the merge failure propagates
            o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

        # Original worktree was still restored despite the temp-cleanup failure.
        assert any(u.get("worktree_path") == WT_PATH for u in _db_updates(o))

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_original_restore_failure_fails_closed(self, mock_gh_cls):
        """#2041 #5: if restoring the original worktree fails, the workflow must
        enter a visible failed state and raise — it must NOT continue on the
        main checkout."""
        wf = _make_workflow()
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        def remove(path):
            if path == WT_PATH:
                return {"removed": path}
            return {"removed": path}

        def add(path, branch):
            if path == WT_PATH:
                raise GitHubOpsError("restore failed")  # restore fails
            return {"worktree_path": path, "branch": branch}

        mock_gh.remove_worktree.side_effect = remove
        mock_gh.add_worktree.side_effect = add
        mock_gh.path_exists_as_user.return_value = False  # triggers the failing add
        mock_gh._run_git = MagicMock(
            return_value=MagicMock(returncode=1, stdout="", stderr="merge boom")
        )
        mock_gh.resolve_commit.return_value = "main-head"
        o._gh = mock_gh

        with pytest.raises(Exception):
            o._resolve_merge_conflicts(mock_gh, BRANCH, PR_NUMBER)

        # Workflow fail-closed: status=failed persisted with an error message.
        assert any(u.get("status") == "failed" and u.get("error_message") for u in _db_updates(o))


# ── contract: verify accepts the real porcelain-parsed shape ──────────────


def test_verify_worktree_restored_accepts_refs_heads_branch_format():
    """``_verify_worktree_restored`` must accept the ``refs/heads/<name>`` shape
    that ``GitHubOps.list_worktrees`` actually emits (locked by the porcelain
    parser test in tests/issues/716). The orchestrator-side tests above mock
    the bare branch name; this locks the cross-module contract so a parser or
    git-version change can't silently break the verify while tests stay green.
    """
    o, _ = _make_orchestrator(_make_workflow())
    gh = MagicMock()
    gh.list_worktrees.return_value = [{"path": WT_PATH, "branch": f"refs/heads/{BRANCH}"}]
    # Must not raise on the porcelain-parsed shape.
    o._verify_worktree_restored(gh, WT_PATH, BRANCH)


def test_verify_worktree_restored_rejects_wrong_branch():
    """A restored worktree registered on the wrong branch must fail verification."""
    o, _ = _make_orchestrator(_make_workflow())
    gh = MagicMock()
    gh.list_worktrees.return_value = [{"path": WT_PATH, "branch": "refs/heads/main"}]
    with pytest.raises(RuntimeError, match="(?i)wrong branch"):
        o._verify_worktree_restored(gh, WT_PATH, BRANCH)


def test_remove_worktree_idempotent_preserves_original_error_when_probe_fails():
    """If the ``list_worktrees`` probe itself errors inside the except handler,
    the ORIGINAL removal error must be re-raised (not masked by the probe's)."""
    from app.modules.workspace.autonomous.github_ops import GitHubOpsError

    o, _ = _make_orchestrator(_make_workflow())
    gh = MagicMock()
    removal_error = GitHubOpsError("remove failed")
    gh.remove_worktree.side_effect = removal_error
    gh.list_worktrees.side_effect = GitHubOpsError("probe also failed")

    with pytest.raises(GitHubOpsError) as exc_info:
        o._remove_worktree_idempotent(gh, WT_PATH)
    assert "remove failed" in str(exc_info.value)
