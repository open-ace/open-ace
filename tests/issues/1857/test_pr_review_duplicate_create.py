"""Tests for idempotent PR creation in _do_pr_review (Issue #1857).

advance() is reentrant — the scheduler may call it again while a PR-review
agent is still running and current_round hasn't been persisted yet (it's
written at the end of the review round, orchestrator.py ~5293). On re-entry
round_num is still 1, so the old code called gh pr create again and hit
"a pull request ... already exists", failing the whole workflow.

Fix: skip create_pr when github_pr_number is already set, and recover
gracefully (reuse the existing PR) if gh reports "already exists" anyway.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1857",
        "user_id": 1,
        "title": "issue-1857",
        "status": "pr_review",
        "current_phase": "pr_review",
        "requirements_text": "Fix encryption doc/code mismatch",
        "requirements_issue_url": "",
        "project_path": "/tmp/repo",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "glm-5",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-1857",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "github_issue_number": 1857,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 2,
        "max_pr_review_rounds": 3,
        "require_full_review_rounds": False,
        "content_language": "zh",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
    return orch, mock_repo


def _make_gh_with_changes():
    """A gh mock where the branch has changes vs main (so PR creation proceeds).

    _do_pr_review checks: rev-parse branch/main (distinct shas), merge-base
    --is-ancestor (returncode 1 = NOT ancestor, so has_changes path runs),
    get_diff_stats (commits>0)."""
    gh = MagicMock()

    def run_git(args, **kw):
        # rev-parse branch_name → branch sha
        if args[:2] == ["rev-parse", "auto-dev/wf-1857"]:
            return MagicMock(stdout="branch-sha-abc\n", returncode=0)
        # rev-parse main → main sha (different)
        if args[:2] == ["rev-parse", "main"]:
            return MagicMock(stdout="main-sha-xyz\n", returncode=0)
        # merge-base --is-ancestor branch main → NOT ancestor (returncode 1)
        if args[:1] == ["merge-base"]:
            return MagicMock(stdout="", returncode=1)
        return MagicMock(stdout="", returncode=0)

    gh._run_git.side_effect = run_git
    gh.get_diff_stats.return_value = {"commits": 1, "additions": 5, "deletions": 1}
    gh.get_current_branch.return_value = "auto-dev/wf-1857"
    gh.git_push.return_value = None
    gh.get_diff.return_value = "diff --git a/app/x.py b/app/x.py\n+new line"
    gh.get_pr_checks.return_value = []  # no CI fails for simplicity
    gh.get_pr_head_sha.return_value = "head-sha-123"
    gh.get_pr.return_value = {"number": 1877, "url": "https://x/pull/1877"}
    return gh


# ── Layer 1: skip create_pr when github_pr_number already set ──────────


def test_re_entry_skips_create_pr_when_pr_already_exists():
    """When advance() re-enters _do_pr_review with round_num=1 but the PR was
    already created (github_pr_number set), create_pr must NOT be called again.
    Reproduces #1857 where the re-entry hit 'already exists' and failed."""
    wf = _make_workflow(
        current_round=0,  # not yet persisted → round_num computes to 1
        github_pr_number=1877,  # PR already created by the first entry
        github_pr_url="https://github.com/open-ace/open-ace/pull/1877",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh_with_changes()
    orch._get_gh = MagicMock(return_value=gh)
    # Short-circuit the review agent + downstream — we only care that
    # create_pr isn't called. Raise to stop execution after the PR-creation
    # block; the assertion is on gh.create_pr before that point.
    orch._run_agent = MagicMock(side_effect=RuntimeError("stop-after-pr-block"))

    try:
        orch._do_pr_review(wf)
    except RuntimeError:
        pass

    gh.create_pr.assert_not_called()
    gh.find_existing_pr.assert_not_called()


# ── Layer 2: recover via find_existing_pr when 'already exists' slips through ─


def test_create_pr_already_exists_recovers_via_find_existing_pr():
    """If gh pr create returns 'already exists' (race past the guard),
    find_existing_pr must locate and reuse the existing PR instead of
    failing the workflow."""
    wf = _make_workflow(current_round=0, github_pr_number=None)
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh_with_changes()
    gh.create_pr.side_effect = GitHubOpsError(
        'gh pr create failed: a pull request for branch "auto-dev/wf-1857" '
        'into branch "main" already exists: '
        "https://github.com/open-ace/open-ace/pull/1877"
    )
    gh.find_existing_pr.return_value = {
        "number": 1877,
        "url": "https://github.com/open-ace/open-ace/pull/1877",
        "title": "[Auto] Dev round 1",
    }
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_agent = MagicMock(side_effect=RuntimeError("stop-after-pr-block"))

    try:
        orch._do_pr_review(wf)
    except RuntimeError:
        pass

    # Recovery happened: workflow updated with the reused PR number.
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    pr_updates = [u for u in updates if u.get("github_pr_number") == 1877]
    assert pr_updates, "reused PR number not persisted to workflow"
    # And the workflow was NOT failed by the GitHubOpsError.
    assert not any(
        u.get("status") == "failed" for u in updates
    ), "workflow wrongly failed on 'already exists' instead of recovering"


def test_create_pr_unrecoverable_error_still_fails():
    """If gh pr create fails for a reason OTHER than 'already exists'
    (and find_existing_pr finds nothing), the workflow must still fail —
    don't mask real errors with the recovery path."""
    wf = _make_workflow(current_round=0, github_pr_number=None)
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh_with_changes()
    gh.create_pr.side_effect = GitHubOpsError("network unreachable")
    gh.find_existing_pr.return_value = None
    orch._get_gh = MagicMock(return_value=gh)

    raised = False
    try:
        orch._do_pr_review(wf)
    except GitHubOpsError:
        raised = True

    assert raised, "non-'already exists' error should propagate, not be masked"
    # A failed pr_created milestone should have been recorded.
    milestone_create_calls = mock_repo.create_milestone.call_args_list
    failed_milestones = [
        c
        for c in milestone_create_calls
        if c.args
        and isinstance(c.args[0], dict)
        and c.args[0].get("status") == "failed"
        and c.args[0].get("milestone_type") == "pr_created"
    ]
    assert failed_milestones, "no failed pr_created milestone recorded"


# ── GitHubOps.find_existing_pr unit tests ───────────────────────────────


def test_find_existing_pr_returns_pr_for_branch():
    gh = GitHubOps.__new__(GitHubOps)
    pr_list_json = '[{"number": 1877, "url": "https://x/pull/1877", "title": "t"}]'
    with patch.object(gh, "_run_gh") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=pr_list_json, stderr="")
        result = gh.find_existing_pr("auto-dev/wf-1857")
    assert result == {"number": 1877, "url": "https://x/pull/1877", "title": "t"}


def test_find_existing_pr_returns_none_when_no_pr():
    gh = GitHubOps.__new__(GitHubOps)
    with patch.object(gh, "_run_gh") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        result = gh.find_existing_pr("auto-dev/wf-1857")
    assert result is None


def test_find_existing_pr_empty_branch_returns_none():
    gh = GitHubOps.__new__(GitHubOps)
    with patch.object(gh, "_run_gh") as mock_run:
        result = gh.find_existing_pr("")
    assert result is None
    mock_run.assert_not_called()
