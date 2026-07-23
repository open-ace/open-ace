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


@pytest.mark.parametrize("recovered_success", [True, False])
def test_stable_session_line_rebinds_once_and_preserves_usage(recovered_success):
    """Recovery keeps the tracking line and combines both attempts' usage."""
    wf = _make_workflow(
        main_session_id="main-track",
        review_session_id="review-track",
        test_session_id="test-track",
    )
    orch, _ = _make_orchestrator(wf)
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._runner.session_manager = MagicMock()
    orch._runner.session_manager.get_session.return_value = MagicMock(
        context={"cli_session_id": "provider-too-large", "keep": "value"}
    )
    orch._runner.session_manager.update_session_fields.return_value = True
    overflow = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text="API Error: 400 Range of input length should be [1, 202752]",
        total_tokens=11,
        total_input_tokens=7,
        total_output_tokens=4,
        request_count=1,
    )
    recovered = AgentTaskResult(
        session_id="main-track",
        success=recovered_success,
        response_text="修复完成" if recovered_success else "",
        error="agent exited 1" if not recovered_success else "",
        total_tokens=23,
        total_input_tokens=17,
        total_output_tokens=6,
        request_count=2,
    )

    def run_agent(**kwargs):
        result = overflow if not kwargs.get("force_fresh") else recovered
        orch._write_phase_usage(kwargs["milestone_id"], result, kwargs.get("prior_usage"))
        return result

    orch._run_agent = MagicMock(side_effect=run_agent)

    result = orch._run_agent_with_context_recovery(
        wf,
        session_line="main",
        milestone_id="ms-pr-fix",
        prompt="fix review findings",
    )

    assert result is recovered
    assert orch._run_agent.call_count == 2
    assert orch._run_agent.call_args_list[0].kwargs["wf"]["main_session_id"] == "main-track"
    retry_kwargs = orch._run_agent.call_args_list[1].kwargs
    assert retry_kwargs["wf"]["main_session_id"] == "main-track"
    assert retry_kwargs["force_fresh"] is True
    assert retry_kwargs["prior_usage"] == {
        "total_tokens": 11,
        "total_input_tokens": 7,
        "total_output_tokens": 4,
        "request_count": 1,
    }
    orch._runner.session_manager.update_session_fields.assert_called_once_with(
        "main-track",
        {
            "cli_session_id": "",
            "context": {"keep": "value"},
            "status": "active",
        },
    )
    orch._update_workflow.assert_called_once_with({"agent_session_id": "", "agent_pid": None})
    orch._accumulate_tokens.assert_called_once_with(overflow)
    usage_update = orch.repo.update_milestone.call_args.args[1]
    assert usage_update == {
        "phase_total_tokens": 34,
        "phase_input_tokens": 24,
        "phase_output_tokens": 10,
        "phase_request_count": 3,
    }
    assert wf["main_session_id"] == "main-track"
    assert wf["review_session_id"] == "review-track"
    assert wf["test_session_id"] == "test-track"


@pytest.mark.parametrize("recovered_success", [True, False])
def test_context_recovery_carries_transient_retry_usage(recovered_success):
    """Transient usage before overflow is read from the persisted milestone."""
    wf = _make_workflow(main_session_id="main-track")
    orch, mock_repo = _make_orchestrator(wf)
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._runner.session_manager = MagicMock()
    orch._runner.session_manager.get_session.return_value = MagicMock(context={})
    orch._runner.session_manager.update_session_fields.return_value = True
    # First _run_agent aggregate: transient 5/3/2/1 + overflow 11/7/4/1.
    mock_repo.get_milestone.return_value = {
        "phase_total_tokens": 16,
        "phase_input_tokens": 10,
        "phase_output_tokens": 6,
        "phase_request_count": 2,
    }
    overflow = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text="API Error: 400 Range of input length should be [1, 202752]",
        total_tokens=11,
        total_input_tokens=7,
        total_output_tokens=4,
        request_count=1,
    )
    recovered = AgentTaskResult(
        session_id="main-track",
        success=recovered_success,
        response_text="fixed" if recovered_success else "",
        error="agent exited 1" if not recovered_success else "",
        total_tokens=23,
        total_input_tokens=17,
        total_output_tokens=6,
        request_count=2,
    )

    def run_agent(**kwargs):
        result = overflow if not kwargs.get("force_fresh") else recovered
        orch._write_phase_usage(kwargs["milestone_id"], result, kwargs.get("prior_usage"))
        return result

    orch._run_agent = MagicMock(side_effect=run_agent)

    result = orch._run_agent_with_context_recovery(
        wf,
        session_line="main",
        milestone_id="ms-pr-fix",
        prompt="fix review findings",
    )

    assert result is recovered
    assert orch._run_agent.call_args_list[1].kwargs["prior_usage"] == {
        "total_tokens": 16,
        "total_input_tokens": 10,
        "total_output_tokens": 6,
        "request_count": 2,
    }
    usage_update = mock_repo.update_milestone.call_args.args[1]
    assert usage_update == {
        "phase_total_tokens": 39,
        "phase_input_tokens": 27,
        "phase_output_tokens": 12,
        "phase_request_count": 4,
    }


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
    gh.has_uncommitted_changes.return_value = False

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


def test_review_fix_refuses_preexisting_dirty_tree_before_agent():
    """Pre-existing edits can never hitchhike on a recovered review fix."""
    wf = _make_workflow(current_phase="pr_review", status="pr_review")
    orch, mock_repo = _make_orchestrator(wf)
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-fix"})
    orch._update_workflow = MagicMock()
    orch._run_agent_with_context_recovery = MagicMock()
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
    orch._run_agent_with_context_recovery.assert_not_called()
    gh.git_add_all.assert_not_called()
    gh.git_commit.assert_not_called()
    gh.git_push.assert_not_called()
    assert (
        "already had uncommitted changes"
        in mock_repo.update_milestone.call_args.args[1]["error_message"]
    )


@pytest.mark.parametrize(
    "result, expected_error",
    [
        (AgentTaskResult(success=False, error="agent process exited 1"), "agent failed"),
        (AgentTaskResult(success=True, response_text=""), "returned no result"),
    ],
)
def test_review_fix_failed_or_empty_agent_never_mutates_git(result, expected_error):
    """All unsuccessful agent outcomes stop before salvage, push, or cap summary."""
    wf = _make_workflow(current_phase="pr_review", status="pr_review")
    orch, mock_repo = _make_orchestrator(wf)
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-fix"})
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._run_agent_with_context_recovery = MagicMock(return_value=result)
    gh = MagicMock()
    gh.has_uncommitted_changes.return_value = False
    gh.get_current_commit.return_value = "sha-before"

    succeeded = orch._apply_pr_review_fix(
        wf,
        gh,
        "B1 must be fixed",
        round_num=3,
        dev_round=1,
        ci_failures=[],
        pr_number=1849,
    )

    assert succeeded is False
    gh.git_add_all.assert_not_called()
    gh.git_commit.assert_not_called()
    gh.git_push.assert_not_called()
    assert expected_error in mock_repo.update_milestone.call_args.args[1]["error_message"]
    assert orch._update_workflow.call_args.args[0]["status"] == "failed"


def test_review_fix_fails_when_committed_head_cannot_be_read():
    """A commit is not pushable until its resulting HEAD is verified."""
    wf = _make_workflow(current_phase="pr_review", status="pr_review")
    orch, _ = _make_orchestrator(wf)
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-fix"})
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._run_agent_with_context_recovery = MagicMock(
        return_value=AgentTaskResult(success=True, response_text="fixed")
    )
    gh = MagicMock()
    gh.has_uncommitted_changes.side_effect = [False, True]
    gh.get_current_commit.side_effect = ["sha-before", "sha-before", RuntimeError("head denied")]

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
    gh.git_commit.assert_called_once()
    gh.git_push.assert_not_called()
    assert "head denied" in orch._update_workflow.call_args.args[0]["error_message"]


@pytest.mark.parametrize("empty_stage", ["review", "summary"])
def test_empty_review_or_summary_fails_closed(empty_stage):
    """Whitespace-only review artifacts never complete a milestone or advance."""
    wf = _make_workflow(
        current_phase="pr_review",
        status="pr_review",
        current_round=0,
        max_pr_review_rounds=1,
        github_pr_number=1849,
    )
    orch, mock_repo = _make_orchestrator(wf)
    milestone_types = []

    def create_milestone(**fields):
        milestone_type = fields.get("milestone_type")
        milestone_types.append(milestone_type)
        return {"milestone_id": f"ms-{milestone_type}"}

    orch._create_milestone = MagicMock(side_effect=create_milestone)
    orch._update_workflow = MagicMock()
    orch._get_pr_review_diff = MagicMock(return_value="diff")
    orch._validate_autonomous_change_scope = MagicMock(return_value="")
    orch._poll_ci_status = MagicMock(return_value=[])
    orch._post_github_comment = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._gh = MagicMock()
    orch._get_gh = MagicMock(return_value=orch._gh)
    orch._gh.get_current_branch.return_value = wf["branch_name"]
    orch._gh.get_diff_stats.return_value = {"commits": 1}

    def run_git(args, check=True):
        if args[:1] == ["rev-parse"]:
            return MagicMock(stdout=f"{args[1]}-sha\n", returncode=0)
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return MagicMock(stdout="", returncode=1)
        return MagicMock(stdout="", returncode=0)

    orch._gh._run_git.side_effect = run_git
    empty = AgentTaskResult(session_id=f"{empty_stage}-track", success=True, response_text="  \n")
    if empty_stage == "review":
        results = [empty]
    else:
        results = [
            AgentTaskResult(
                session_id="review-track",
                success=True,
                response_text=(
                    '批准\nREVIEW_RESULT: {"verdict":"APPROVE",' '"blocking_findings":[]}'
                ),
            ),
            empty,
        ]
    orch._run_agent_with_context_recovery = MagicMock(side_effect=results)

    orch._do_pr_review(wf)

    assert not any(
        call.args[0].get("status") == "reporting" for call in orch._update_workflow.call_args_list
    )
    assert any(
        call.args[0].get("status") == "failed"
        and "returned no result" in call.args[0].get("error_message", "")
        for call in orch._update_workflow.call_args_list
    )
    failed_ms_id = f"ms-{'pr_reviewed' if empty_stage == 'review' else 'pr_review_summary'}"
    assert any(
        call.args[0] == failed_ms_id and call.args[1].get("status") == "failed"
        for call in mock_repo.update_milestone.call_args_list
    )
    if empty_stage == "review":
        assert "pr_review_summary" not in milestone_types


def test_cap_round_commit_failure_does_not_create_summary_or_enter_report():
    """A failed fix commit on the last round must stop the state machine."""
    wf = _make_workflow(
        current_phase="pr_review",
        status="pr_review",
        current_round=0,
        max_pr_review_rounds=1,
        github_pr_number=1849,
    )
    orch, _ = _make_orchestrator(wf)
    milestone_types = []

    def create_milestone(**fields):
        milestone_types.append(fields.get("milestone_type"))
        return {"milestone_id": f"ms-{fields.get('milestone_type')}"}

    orch._create_milestone = MagicMock(side_effect=create_milestone)
    orch._update_workflow = MagicMock()
    orch._get_pr_review_diff = MagicMock(return_value="diff")
    orch._validate_autonomous_change_scope = MagicMock(return_value="")
    orch._poll_ci_status = MagicMock(return_value=[])
    orch._post_github_comment = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._gh = MagicMock()
    orch._get_gh = MagicMock(return_value=orch._gh)
    orch._gh.get_current_branch.return_value = wf["branch_name"]
    orch._gh.get_diff_stats.return_value = {"commits": 1}
    orch._gh.has_uncommitted_changes.side_effect = [False, True]
    orch._gh.get_current_commit.side_effect = ["sha-before", "sha-before"]
    orch._gh.git_commit.side_effect = RuntimeError("commit denied")

    def run_git(args, check=True):
        if args[:1] == ["rev-parse"]:
            return MagicMock(stdout=f"{args[1]}-sha\n", returncode=0)
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return MagicMock(stdout="", returncode=1)
        return MagicMock(stdout="", returncode=0)

    orch._gh._run_git.side_effect = run_git
    orch._run_agent_with_context_recovery = MagicMock(
        side_effect=[
            AgentTaskResult(
                session_id="review-track",
                success=True,
                response_text=(
                    '发现阻塞问题\nREVIEW_RESULT: {"verdict":"REQUEST_CHANGES",'
                    '"blocking_findings":["B1"]}'
                ),
            ),
            AgentTaskResult(
                session_id="main-track",
                success=True,
                response_text="fixed",
            ),
        ]
    )

    orch._do_pr_review(wf)

    orch._gh.git_commit.assert_called_once()
    # The sole push is the normal pre-review branch sync; no fix push follows.
    orch._gh.git_push.assert_called_once_with(branch=wf["branch_name"], force_with_lease=True)
    assert "pr_review_summary" not in milestone_types
    assert not any(
        call.args[0].get("status") == "reporting" for call in orch._update_workflow.call_args_list
    )
    assert any(
        call.args[0].get("status") == "failed"
        and "Unable to commit PR review fix" in call.args[0].get("error_message", "")
        for call in orch._update_workflow.call_args_list
    )


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


def test_normal_ci_failure_preserves_runner_error_without_fresh_retry():
    """A non-overflow runner failure must remain actionable in the milestone.

    It must not trigger a fresh retry, and the no-change fallback must not
    overwrite the first-class runner error.
    """
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
    assert "failed before producing code changes" in final_updates["error_message"]
    assert "agent produced no output" in final_updates["error_message"]


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
