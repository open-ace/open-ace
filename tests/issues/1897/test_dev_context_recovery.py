"""Tests for development-phase context-overflow recovery.

When the dev agent resumes the ``main`` session and the accumulated
conversation exceeds the model's input-token limit (e.g. GLM ``400
InvalidParameter: Range of input length``), the orchestrator must
retry the SAME dev attempt on a fresh minimal-context session (no
``--resume``) while preserving the 3-session topology (main/review/test).

This mirrors the CI repair and PR review fix paths, which already use
``_run_agent_with_context_recovery`` for the same scenario.

See orchestrator._run_development_agent + _run_agent_with_context_recovery.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-dev-overflow",
        "user_id": 1,
        "title": "Test dev context recovery",
        "status": "developing",
        "requirements_text": "Build feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/p",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "glm-5",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/x",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/p",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "main_session_id": "main-track",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data, milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = milestones or []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-new",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo.update_milestone.return_value = {}
        mock_repo.update_workflow_tokens.return_value = None
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.get_current_branch.return_value = wf_data["branch_name"]
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_commit_diff_stats.return_value = {
            "additions": 0,
            "deletions": 0,
            "files": 0,
            "commits": 1,
        }
        orch._gh.get_diff_stats.return_value = {}
        orch._gh.has_uncommitted_changes.return_value = False
    return orch, mock_repo


_OVERFLOW_ERROR = (
    "API Error: 400 <400> InternalError.Algo.InvalidParameter: "
    "Range of input length should be [1, 202752]"
)


# ── Dev phase delegates to _run_agent_with_context_recovery ────────────


def test_dev_phase_uses_context_recovery_wrapper():
    """The dev agent call must go through _run_agent_with_context_recovery
    (not _run_agent directly) so context overflow is auto-recovered."""
    plan_ms = {
        "milestone_id": "ms-plan",
        "plan_content": "1. Implement",
        "status": "completed",
    }
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

    orch._run_agent_with_context_recovery = MagicMock(
        return_value=AgentTaskResult(
            session_id="main-track",
            success=True,
            response_text="done",
        )
    )
    orch._accumulate_tokens = MagicMock()
    orch._post_dev_completion_comment = MagicMock()
    orch._get_gh = MagicMock(return_value=orch._gh)
    # commit_before="aaa", commit_after="bbb" => sha_changed is True.
    orch._gh.get_current_commit.side_effect = ["aaa1111", "bbb2222"]
    orch._runtime_environment_gate = MagicMock(return_value="")

    orch._run_development_agent(wf, dev_round=1, gh=orch._gh)

    # Dev phase must call _run_agent_with_context_recovery, not _run_agent.
    orch._run_agent_with_context_recovery.assert_called_once()
    call_kwargs = orch._run_agent_with_context_recovery.call_args.kwargs
    assert call_kwargs["session_line"] == "main"
    assert call_kwargs["wf"] is wf


# ── Context overflow in dev is recovered on a fresh transcript ─────────


def test_dev_context_overflow_recovers_on_fresh_session():
    """When the resumed main session overflows, the dev agent retries on a
    fresh provider transcript bound to the SAME tracking id (3-session
    topology preserved), and dev proceeds if the retry commits code."""
    plan_ms = {
        "milestone_id": "ms-plan",
        "plan_content": "1. Implement",
        "status": "completed",
    }
    wf = _make_workflow(main_session_id="main-track")
    orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

    # Simulate the context-recovery flow at the _run_agent level:
    # 1st call (resume) → overflow; 2nd call (force_fresh) → success.
    overflow_result = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text=_OVERFLOW_ERROR,
        total_tokens=11,
        total_input_tokens=7,
        total_output_tokens=4,
        request_count=1,
    )
    recovered_result = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text="implemented feature",
        total_tokens=23,
        total_input_tokens=17,
        total_output_tokens=6,
        request_count=2,
    )

    orch._runner = MagicMock()
    orch._runner.session_manager = MagicMock()
    orch._runner.session_manager.get_session.return_value = MagicMock(
        context={"cli_session_id": "provider-too-large"}
    )
    orch._runner.session_manager.update_session_fields.return_value = True
    orch._accumulate_tokens = MagicMock()
    orch._post_dev_completion_comment = MagicMock()
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._get_gh = MagicMock(return_value=orch._gh)
    orch._update_workflow = MagicMock()
    # commit_before="aaa", commit_after="bbb" => sha_changed is True.
    orch._gh.get_current_commit.side_effect = ["aaa1111", "bbb2222"]

    call_count = {"n": 0}

    def run_agent_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: resume main session → overflow.
            assert not kwargs.get("force_fresh")
            return overflow_result
        # Second call: force_fresh retry → success.
        assert kwargs.get("force_fresh") is True
        return recovered_result

    orch._run_agent = MagicMock(side_effect=run_agent_side_effect)

    orch._run_development_agent(wf, dev_round=1, gh=orch._gh)

    # _run_agent called twice: once normal, once with force_fresh.
    assert orch._run_agent.call_count == 2
    # Provider mapping cleared on the SAME tracking id (3-session preserved).
    orch._runner.session_manager.update_session_fields.assert_called_once_with(
        "main-track",
        {
            "cli_session_id": "",
            "context": {},
            "status": "active",
        },
    )
    # Dev must NOT be marked failed — recovery succeeded.
    workflow_updates = [c.args[0] for c in orch._update_workflow.call_args_list if c.args]
    assert not any(
        u.get("status") == "failed" and "no code changes" in (u.get("error_message") or "")
        for u in workflow_updates
    ), "dev should have recovered from context overflow, not failed"
    # main_session_id preserved (3-session topology).
    assert wf["main_session_id"] == "main-track"


# ── Dev context overflow recovery fails closed when retry also fails ───


def test_dev_context_overflow_recovery_failure_fails_closed():
    """If the fresh retry ALSO produces no code changes, dev must fail
    cleanly (not silently proceed)."""
    plan_ms = {
        "milestone_id": "ms-plan",
        "plan_content": "1. Implement",
        "status": "completed",
    }
    wf = _make_workflow(main_session_id="main-track")
    orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

    overflow_result = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text=_OVERFLOW_ERROR,
    )
    empty_result = AgentTaskResult(
        session_id="main-track",
        success=True,
        response_text="",
    )

    orch._runner = MagicMock()
    orch._runner.session_manager = MagicMock()
    orch._runner.session_manager.get_session.return_value = MagicMock(context={})
    orch._runner.session_manager.update_session_fields.return_value = True
    orch._accumulate_tokens = MagicMock()
    orch._post_dev_completion_comment = MagicMock()
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._get_gh = MagicMock(return_value=orch._gh)
    orch._update_workflow = MagicMock()
    # commit unchanged → sha_changed is False.
    orch._gh.get_current_commit.side_effect = ["aaa1111", "aaa1111"]
    # Branch has pre-existing divergence vs origin/main so the "no code
    # changes" failure path fires (commit_sha == commit_before).
    orch._gh.get_diff_stats.return_value = {"commits": 1}

    orch._run_agent = MagicMock(side_effect=[overflow_result, empty_result])

    orch._run_development_agent(wf, dev_round=1, gh=orch._gh)

    # Dev must be marked failed.
    workflow_updates = [c.args[0] for c in orch._update_workflow.call_args_list if c.args]
    assert any(
        u.get("status") == "failed" for u in workflow_updates
    ), "dev should fail when context recovery retry produces no code changes"
