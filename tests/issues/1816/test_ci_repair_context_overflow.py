"""Regression tests for CI repair context-overflow recovery (Issue #1816).

When a CI repair attempt on the resumed ``main`` session fails because the
accumulated conversation exceeds the model's input-token limit (GLM ``400
InvalidParameter: Range of input length``), the orchestrator must retry the
SAME attempt on a fresh minimal-context session (no ``--resume``), injecting
prior CI repair failures so the agent avoids repeating them.

See orchestrator._run_merge_ci_repair + _is_context_overflow.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1816",
        "user_id": 5,
        "title": "issue-1816",
        "status": "merging",
        "current_phase": "merge",
        "requirements_text": "Fix change-password rate limiting",
        "requirements_issue_url": "",
        "project_path": "/tmp/repo",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "glm-5",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-1816",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "github_issue_number": 1816,
        "github_pr_number": 1849,
        "github_pr_url": "",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 2,
        "max_pr_review_rounds": 3,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "ci_repair_attempts": 1,
        "ci_repair_context": "### test (3.9)\n- 状态: failure",
        "last_ci_failure_head_sha": "",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data, prior_milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        # _collect_prior_ci_repair_failures queries phase="merge"; other callers
        # (e.g. _create_milestone's idempotency guard) may query with other
        # filters — return prior_milestones only for the merge query.
        if prior_milestones is None:
            prior_milestones = []
        mock_repo.list_milestones.side_effect = lambda *a, **k: (
            prior_milestones
            if (k.get("phase") == "merge" or (len(a) >= 2 and a[1] == "merge"))
            else []
        )
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


def _make_gh(commit_before="sha-old", commit_after="sha-new"):
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = commit_before
    gh.get_current_commit.side_effect = [commit_before, commit_after]
    gh.get_current_branch.return_value = "auto-dev/wf-1816"
    gh.get_commit_diff_stats.return_value = {
        "files": 1,
        "additions": 3,
        "deletions": 1,
    }
    return gh


_FAILED_CHECKS = [
    {"name": "test (3.9)", "state": "failure", "bucket": "fail", "link": "https://example.com"}
]


# ── Detection: _is_context_overflow ──────────────────────────────────────


@pytest.mark.parametrize(
    "error,response_text,expected,success",
    [
        # GLM-5 actual signature observed in production (issue #1816 workflow #2)
        (
            "API Error: 400 <400> InternalError.Algo.InvalidParameter: "
            "Range of input length should be [1, 202752]",
            "",
            True,
            False,
        ),
        # OpenAI signature
        ("This model's maximum context length is 8192 tokens.", "", True, False),
        ("too many input tokens", "", True, False),
        # Anthropic signature
        ("prompt is too long: 250000 tokens > 200000 maximum", "", True, False),
        # In response_text rather than error
        ("", "API Error: 400 Range of input length should be [1, 202752]", True, False),
        # Not an overflow — must NOT match
        ("CI repair failed: agent produced no code changes", "", False, False),
        ("API Error: 429 too many requests", "", False, False),
        ("", "normal plan output discussing input validation", False, False),
        # Successful prose must not match merely because its error field mentions length.
        ("Range of input length should be [1, 202752]", "", False, True),
        # A provider can exit zero while returning a terminal API error envelope.
        (
            "",
            "API Error: 400 Range of input length should be [1, 202752]",
            True,
            True,
        ),
    ],
)
def test_is_context_overflow_matches_provider_signatures(error, response_text, expected, success):
    """The detector must catch GLM/OpenAI/Anthropic overflow phrasings and
    ignore transient errors, generic failures, and successful results."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    result = AgentTaskResult(success=success, error=error, response_text=response_text)
    assert AutonomousOrchestrator._is_context_overflow(result) is expected


def test_stable_session_line_rotates_once_after_context_overflow():
    """PR review/fix/summary calls keep one logical line but replace its head."""
    wf = _make_workflow(main_session_id="main-too-large")
    orch, _ = _make_orchestrator(wf)
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    overflow = AgentTaskResult(
        session_id="main-too-large",
        success=True,
        response_text="API Error: 400 Range of input length should be [1, 202752]",
    )
    recovered = AgentTaskResult(
        session_id="main-replacement",
        success=True,
        response_text="修复完成",
    )
    orch._run_agent = MagicMock(side_effect=[overflow, recovered])

    result = orch._run_agent_with_context_recovery(
        wf,
        session_line="main",
        milestone_id="ms-pr-fix",
        prompt="fix review findings",
    )

    assert result is recovered
    assert orch._run_agent.call_count == 2
    assert orch._run_agent.call_args_list[0].kwargs["wf"]["main_session_id"] == "main-too-large"
    assert orch._run_agent.call_args_list[1].kwargs["wf"]["main_session_id"] == ""
    orch._update_workflow.assert_called_once_with(
        {"main_session_id": "", "agent_session_id": "", "agent_pid": None}
    )
    orch._accumulate_tokens.assert_called_once_with(overflow)


def test_review_fix_double_overflow_fails_before_committing_dirty_tree():
    """A terminal API envelope must never commit unrelated pending changes."""
    wf = _make_workflow(current_phase="pr_review", status="pr_review")
    orch, mock_repo = _make_orchestrator(wf)
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-fix"})
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    overflow = AgentTaskResult(
        session_id="replacement-overflowed",
        success=True,
        response_text="API Error: 400 Range of input length should be [1, 202752]",
    )
    orch._run_agent_with_context_recovery = MagicMock(return_value=overflow)
    gh = MagicMock()
    gh.has_uncommitted_changes.return_value = True

    succeeded = orch._apply_pr_review_fix(
        wf,
        gh,
        "B1 must be fixed",
        round_num=1,
        dev_round=1,
        ci_failures=[],
        pr_number=1849,
    )

    assert succeeded is False
    gh.git_add_all.assert_not_called()
    gh.git_commit.assert_not_called()
    milestone_update = mock_repo.update_milestone.call_args.args[1]
    assert milestone_update["status"] == "failed"
    assert "context recovery" in milestone_update["error_message"]
    workflow_update = orch._update_workflow.call_args.args[0]
    assert workflow_update["status"] == "failed"


# ── End-to-end: _run_merge_ci_repair switches to fresh on overflow ──────


def test_context_overflow_does_not_repeat_same_fresh_prompt():
    """A fresh-session overflow fails once instead of retrying a longer prompt."""
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh()
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()

    overflow = AgentTaskResult(
        success=False,
        error="API Error: 400 <400> InternalError.Algo.InvalidParameter: "
        "Range of input length should be [1, 202752]",
    )
    orch._run_agent = MagicMock(return_value=overflow)

    orch._run_merge_ci_repair(wf, gh, 1849, _FAILED_CHECKS)

    assert orch._run_agent.call_count == 1
    assert orch._run_agent.call_args_list[0].kwargs["session_line"] == "fresh"
    gh.reset_hard_to_head.assert_called_once()
    final_updates = mock_repo.update_workflow.call_args.args[1]
    assert final_updates["status"] == "failed"
    assert "context overflow" in final_updates["error_message"]


def test_initial_fresh_prompt_injects_prior_failures():
    prior_failure = {
        "milestone_id": "ms-prior",
        "milestone_type": "ci_repair_applied",
        "status": "failed",
        "title": "CI repair attempt 1 for PR #1849",
        "error_message": "CI repair failed: agent produced no code changes",
        "result_summary": "尝试修改 auth_service.py 的 rate limiter 但 test (3.9) 仍失败: ImportError",
    }
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf, prior_milestones=[prior_failure])
    gh = _make_gh()
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()

    overflow = AgentTaskResult(
        success=False,
        error="Range of input length should be [1, 202752]",
    )
    orch._run_agent = MagicMock(return_value=overflow)

    orch._run_merge_ci_repair(wf, gh, 1849, _FAILED_CHECKS)

    fresh_prompt = orch._run_agent.call_args_list[0].kwargs["prompt"]
    # Header signalling "don't repeat"
    assert "请勿重复同样的修法" in fresh_prompt
    # Full prior error_message injected verbatim (not summarized)
    assert "CI repair failed: agent produced no code changes" in fresh_prompt
    # Full prior result_summary injected verbatim
    assert "ImportError" in fresh_prompt


def test_context_overflow_failure_not_injected():
    """A prior failure whose body matches the context-overflow signature must
    be filtered out — the agent produced nothing in that round, so there is
    nothing actionable to avoid repeating."""
    overflow_failure = {
        "milestone_id": "ms-overflow",
        "milestone_type": "ci_repair_applied",
        "status": "failed",
        "title": "CI repair attempt 1 for PR #1849",
        "error_message": "API Error: 400 Range of input length should be [1, 202752]",
        "result_summary": "",
    }
    orch, _ = _make_orchestrator(_make_workflow(), prior_milestones=[overflow_failure])

    prior = orch._collect_prior_ci_repair_failures()
    assert prior == []


def test_normal_ci_failure_does_not_trigger_fresh_retry():
    """A non-overflow agent failure (no code changes, no context-length text)
    must NOT trigger the fresh retry — it should fall through to the existing
    failure path and fail the workflow as before."""
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh(commit_before="sha-old", commit_after="sha-old")  # no new commit
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()

    # Generic failure, no overflow signature.
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(
            success=False,
            error="agent produced no output",
        )
    )

    orch._run_merge_ci_repair(wf, gh, 1849, _FAILED_CHECKS)

    # Only one call (main); no fresh retry.
    assert orch._run_agent.call_count == 1
    assert orch._run_agent.call_args_list[0].kwargs["session_line"] == "fresh"
    gh.reset_hard_to_head.assert_not_called()
    # Existing behavior preserved: workflow failed.
    final_updates = mock_repo.update_workflow.call_args.args[1]
    assert final_updates["status"] == "failed"
    assert "no code changes" in final_updates["error_message"]


def test_single_fresh_overflow_preserves_signal():
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh(commit_before="sha-old", commit_after="sha-old")  # no new commit
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()

    overflow_err = (
        "API Error: 400 <400> InternalError.Algo.InvalidParameter: "
        "Range of input length should be [1, 202752]"
    )
    overflow = AgentTaskResult(success=False, error=overflow_err)
    orch._run_agent = MagicMock(return_value=overflow)

    orch._run_merge_ci_repair(wf, gh, 1849, _FAILED_CHECKS)

    assert orch._run_agent.call_count == 1
    assert orch._run_agent.call_args_list[0].kwargs["session_line"] == "fresh"
    # Workflow failed, not stuck retrying.
    final_updates = mock_repo.update_workflow.call_args.args[1]
    assert final_updates["status"] == "failed"
    # Overflow signal preserved in error_message so the next round filters it
    # (NOT the generic "no code changes" message).
    assert "context overflow" in final_updates["error_message"]
    assert "Range of input length" in final_updates["error_message"]
    # Verify the stored error_message would be matched by the overflow regex,
    # so _collect_prior_ci_repair_failures filters it on the next round.
    from app.modules.workspace.autonomous.orchestrator import _CONTEXT_OVERFLOW_RE

    assert _CONTEXT_OVERFLOW_RE.search(final_updates["error_message"])
