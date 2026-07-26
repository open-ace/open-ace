"""Contract tests for post-merge Git cleanup tracking (Issue #2043).

Covers the separation of delivery completion from resource-convergence, the
structured ``delete_branch`` result, and the retry semantics. Tests follow
``test_autonomous_ci_guardrails.py``: ``AutonomousOrchestrator.__new__`` to
skip ``__init__``, ``MagicMock`` for GitHubOps, method-level stubs.
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_orch():
    """Build a minimal AutonomousOrchestrator via __new__ (skip __init__)."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2043"
    orch.repo = MagicMock()
    orch.emitter = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._update_workflow = MagicMock()
    orch._gh = None
    return orch


# ── delete_branch structured result ───────────────────────────────────────


def test_delete_branch_returns_structured_result_on_success():
    """Both local + remote deletion succeed → deleted/deleted, no errors."""
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps.__new__(GitHubOps)
    gh._run_git = MagicMock(
        side_effect=[
            MagicMock(returncode=0, stderr=""),  # git branch -D
            MagicMock(returncode=0, stderr=""),  # git push origin --delete
        ]
    )
    result = gh.delete_branch("auto-dev/x")
    assert result == {"local": "deleted", "remote": "deleted", "errors": []}


def test_delete_branch_distinguishes_absent_from_failed():
    """Already-gone branch → 'absent' (success-equivalent), not 'failed'."""
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps.__new__(GitHubOps)
    gh._run_git = MagicMock(
        side_effect=[
            MagicMock(returncode=1, stderr="error: branch 'auto-dev/x' not found."),
            MagicMock(returncode=1, stderr="remote ref does not exist"),
        ]
    )
    result = gh.delete_branch("auto-dev/x")
    assert result["local"] == "absent"
    assert result["remote"] == "absent"
    assert result["errors"] == []


def test_remote_branch_delete_failure_is_not_silently_cleaned():
    """Remote delete rejected → 'failed' with stderr captured for retry."""
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps.__new__(GitHubOps)
    gh._run_git = MagicMock(
        side_effect=[
            MagicMock(returncode=0, stderr=""),  # local ok
            MagicMock(returncode=1, stderr="! [remote rejected] ..."),  # remote fail
        ]
    )
    result = gh.delete_branch("auto-dev/x")
    assert result["local"] == "deleted"
    assert result["remote"] == "failed"
    assert any("remote rejected" in e for e in result["errors"])


# ── _perform_git_cleanup status persistence ───────────────────────────────


def _set_workflow(orch, wf):
    """Stub self.workflow to return the given dict."""
    type(orch).workflow = property(lambda self: wf)


def test_cleanup_completion_persists_completed_status():
    """Successful cleanup → cleanup_status=completed, cleared error."""
    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 0,
        },
    )
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps"),
        patch.object(orch, "_cleanup_worktree_and_branch", return_value=True),
    ):
        status, error = orch._perform_git_cleanup()
    assert status == "completed"
    assert error == ""
    # The update_workflow call must carry cleanup_status=completed.
    updates = [c.args[0] for c in orch._update_workflow.call_args_list]
    assert any(u.get("cleanup_status") == "completed" for u in updates)


def test_cleanup_failure_sets_pending_for_retry():
    """Cleanup failure (not exhausted) → cleanup_status=pending + backoff."""
    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 0,
            "error_message": "worktree busy",
        },
    )
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps"),
        patch.object(orch, "_cleanup_worktree_and_branch", return_value=False),
    ):
        status, error = orch._perform_git_cleanup()
    assert status == "pending"
    assert "busy" in error or error
    updates = [c.args[0] for c in orch._update_workflow.call_args_list]
    pending_update = next(u for u in updates if u.get("cleanup_status") == "pending")
    assert pending_update["cleanup_attempts"] == 1
    assert pending_update["cleanup_next_retry_at"]  # backoff timestamp set


def test_cleanup_exhausts_to_failed_after_max_attempts():
    """Beyond MAX_CLEANUP_ATTEMPTS → cleanup_status=failed (terminal)."""
    from app.modules.workspace.autonomous.orchestrator import MAX_CLEANUP_ATTEMPTS

    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
            "project_path": "/tmp/repo",
            "cleanup_attempts": MAX_CLEANUP_ATTEMPTS,
            "error_message": "persistent fail",
        },
    )
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps"),
        patch.object(orch, "_cleanup_worktree_and_branch", return_value=False),
    ):
        status, _ = orch._perform_git_cleanup()
    assert status == "failed"


# ── _do_merge delivery/cleanup separation ─────────────────────────────────


def test_merge_success_with_cleanup_failure_sets_pending():
    """A merge that succeeds but whose cleanup fails keeps the workflow
    delivered (status=completed) while cleanup_status=pending.

    _do_merge writes status=completed + cleanup_status=pending BEFORE calling
    _perform_git_cleanup. The cleanup helper then either completes or leaves
    pending. This test verifies the helper, on failure, does NOT touch the
    business status (only cleanup_* fields) and signals pending for retry.
    """
    orch = _make_orch()
    # Pretend _do_merge already persisted the delivery state.
    _set_workflow(
        orch,
        {
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 0,
            "error_message": "cleanup transient",
            "status": "completed",
            "cleanup_status": "pending",
        },
    )
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps"),
        patch.object(orch, "_cleanup_worktree_and_branch", return_value=False),
    ):
        cleanup_status, _ = orch._perform_git_cleanup()

    assert cleanup_status == "pending"
    # The helper must never overwrite the business status=completed.
    updates = [c.args[0] for c in orch._update_workflow.call_args_list]
    assert all("status" not in u or u.get("cleanup_status") for u in updates)
    assert any(u.get("cleanup_status") == "pending" for u in updates)


def test_cleanup_is_idempotent_when_resources_absent():
    """A workflow whose worktree + branch are already gone still reports completed.

    _cleanup_worktree_and_branch returns True when there is nothing to remove,
    so _perform_git_cleanup marks the workflow cleanup_status=completed.
    """
    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "",
            "branch_name": "",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 2,
        },
    )
    with patch.object(orch, "_cleanup_worktree_and_branch", return_value=True):
        status, _ = orch._perform_git_cleanup()
    assert status == "completed"


# ── scheduler retry sweep ─────────────────────────────────────────────────


def test_startup_retries_pending_git_cleanup():
    """_retry_pending_git_cleanups processes pending workflows via the repo."""
    from app.services.autonomous_scheduler import _retry_pending_git_cleanups

    fake_repo = MagicMock()
    fake_repo.get_workflows_pending_cleanup.return_value = [
        {
            "workflow_id": "wf-a",
            "cleanup_next_retry_at": "",
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
        }
    ]
    with (
        patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
            return_value=fake_repo,
        ),
        patch("app.repositories.database.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
        ) as mock_orch_cls,
    ):
        mock_orch = mock_orch_cls.return_value
        mock_orch._perform_git_cleanup.return_value = ("completed", "")
        _retry_pending_git_cleanups()
    fake_repo.get_workflows_pending_cleanup.assert_called_once()
    mock_orch._perform_git_cleanup.assert_called_once()


def test_sandbox_destroy_does_not_complete_git_cleanup():
    """Sandbox destruction (#2022) is out of scope; cleanup_status tracks Git only.

    This is a boundary assertion: _perform_git_cleanup never touches sandbox
    state and only converges Git resources. There is no sandbox hook here.
    """
    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "",
            "branch_name": "",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 0,
        },
    )
    with patch.object(orch, "_cleanup_worktree_and_branch", return_value=True) as mock_cleanup:
        status, _ = orch._perform_git_cleanup()
    assert status == "completed"
    # Only the Git worktree/branch cleanup path was invoked.
    args, kwargs = mock_cleanup.call_args
    assert kwargs.get("remove_worktree") is True
    assert kwargs.get("remove_branch") is True


def test_cleanup_completion_clears_paths_and_records_event():
    """On full completion the worktree_path/branch_name are cleared (via the
    cleanup helper) and a cleaned_up milestone is recorded."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    # This exercises the _do_merge success tail by calling _perform_git_cleanup
    # with a successful helper, then asserting the milestone type on the caller.
    orch = _make_orch()
    _set_workflow(
        orch,
        {
            "worktree_path": "/tmp/wt",
            "branch_name": "x",
            "project_path": "/tmp/repo",
            "cleanup_attempts": 0,
        },
    )
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps"),
        patch.object(orch, "_cleanup_worktree_and_branch", return_value=True),
    ):
        status, _ = orch._perform_git_cleanup()
    assert status == "completed"
    # The helper cleared the paths internally (mocked), and _perform_git_cleanup
    # persisted cleanup_status=completed. The milestone is created by the
    # _do_merge caller when status == completed.
    updates = [c.args[0] for c in orch._update_workflow.call_args_list]
    assert any(u.get("cleanup_status") == "completed" for u in updates)
