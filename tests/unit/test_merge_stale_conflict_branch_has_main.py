"""Regression: a stale conflict signal on a branch that already contains main
must defer, not drive a no-op resolve that terminally fails.

Workflow e274ec0e (issue #2467, PR #2501) failed with "Merge resolution made
no commit" after a prior merge cycle had already pushed a resolved branch
(the branch now contained main). A later cycle still saw GitHub's conflict-
rejection text; the ``branch_contains_main`` probe was skipped because it
only fired on the cache-derived ``dirty`` path (``mergeable_state == "dirty"
and not is_conflict_rejection and mergeable is not False``).
``resolve_merge_conflicts`` was re-entered, ``git merge`` ran "Already up to
date", and the workflow terminally failed — even though the PR was genuinely
mergeable (it later merged).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator, WorkflowPaused


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-2467",
        "title": "stale conflict on branch-with-main (#2467)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/e274ec0e",
        "worktree_path": "",
        "preferred_worktree_path": "/srv/repo/.worktrees/wf-2467",
        "project_path": "/srv/repo",
        "workspace_type": "local",
        "current_phase": "merge",
        "status": "merging",
        "github_pr_number": 2501,
        "github_issue_number": 2467,
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
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    o._update_workflow = MagicMock()
    o._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    o._write_phase_usage = MagicMock()
    o._validate_pre_merge_change_scope = MagicMock(return_value="")
    o._sync_failed_pr_with_main = MagicMock(return_value=False)
    _now = datetime.now(timezone.utc)
    o._evidence.resolve_verified_pr_head = MagicMock(
        return_value=Evidence(
            source="github_api",
            subject="pr_head",
            verdict=Verdict.CONFIRMED,
            observed_at=_now,
            verified_at=_now,
            verification_method="test-stub",
            commit_shas=("pr-head-sha",),
        )
    )
    return o


@pytest.mark.regression
@pytest.mark.issue(2467)
@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_conflict_text_on_branch_with_main_defers_not_resolves(mock_gh_cls):
    """branch_contains_main=True + conflict-rejection text → defer (retry),
    never call resolve_merge_conflicts (which would no-op "Already up to date"
    and fail with "made no commit").
    """
    o = _make_orchestrator(_make_workflow())
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    o._gh = mock_gh
    mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
    mock_gh.get_pr_merge_state.return_value = {
        "mergeable": True,
        "mergeable_state": "dirty",
    }
    # merge_pr rejects with a conflict-rejection text (is_conflict_rejection=True,
    # is_policy_rejection=False) — the exact path the old probe skipped.
    mock_gh.merge_pr.side_effect = [
        GitHubOpsError("gh pr merge 2501 failed: the merge commit cannot be cleanly created"),
    ]
    o._branch_contains_main = MagicMock(return_value=True)
    o._resolve_merge_conflicts = MagicMock()

    # _do_merge is a test-compat shim that commits the phase result and returns
    # None on retry (only pause re-raises WorkflowPaused). A clean return here
    # — no WorkflowPaused, no terminal raise — is the deferral signal.
    o._do_merge(_make_workflow())

    # Branch already has main → conflict signal is stale → defer, never resolve.
    o._resolve_merge_conflicts.assert_not_called()
    o._branch_contains_main.assert_called_once()
    # Retry keeps the workflow in 'merging' (no terminal failure or pause).
    updates = [c.args[0] for c in o._update_workflow.call_args_list if c.args]
    assert not any(u.get("status") in ("failed", "paused") for u in updates)


@pytest.mark.regression
@pytest.mark.issue(2467)
@patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
def test_policy_rejection_on_branch_with_main_still_pauses(mock_gh_cls):
    """branch_contains_main=True + POLICY rejection → still fall through to the
    policy handler (manual-recovery pause). The probe must not turn a genuine
    policy block into a silent retry just because the branch happens to have main.
    """
    o = _make_orchestrator(_make_workflow())
    mock_gh = MagicMock()
    mock_gh_cls.return_value = mock_gh
    o._gh = mock_gh
    mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
    mock_gh.get_pr_merge_state.return_value = {
        "mergeable": True,
        "mergeable_state": "dirty",
    }
    mock_gh.merge_pr.side_effect = [
        GitHubOpsError("gh pr merge 2501 failed: the base branch policy prohibits the merge"),
    ]
    o._branch_contains_main = MagicMock(return_value=True)
    o._resolve_merge_conflicts = MagicMock()

    with pytest.raises(WorkflowPaused, match="Merge blocked by repository policy"):
        o._do_merge(_make_workflow())

    # The ancestry probe ran (proving the branch-has-main path was taken, not
    # some other pause route), and no git conflict means resolution never ran.
    o._branch_contains_main.assert_called_once()
    o._resolve_merge_conflicts.assert_not_called()
