"""Tests for development-phase salvage logic (issue #723).

Covers Bug #1: when the dev agent reports failure (e.g. a subprocess timeout)
but DID commit real code this session (``sha_changed``), the orchestrator must
salvage the work and proceed to tests instead of marking the workflow failed.
This is exactly what happened to issue #723: claude-code committed a full
3721-line implementation, the subprocess hit the 1h timeout, and the workflow
was discarded purely on ``not result.success``.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-723",
        "user_id": 1,
        "title": "Test",
        "status": "developing",
        "requirements_text": "Build feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/p",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
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
    }
    base.update(overrides)
    return base


def _agent_result(success=True, error=None, text="dev output"):
    return AgentTaskResult(
        session_id="sess-dev",
        response_text=text,
        visible_response_text=text,
        success=success,
        error=error,
    )


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


# ── Salvage (positive): failure + real commit -> proceeds ─────────────


def test_dev_salvaged_on_timeout_with_commit():
    """Bug #1: agent times out but committed code -> NOT failed, proceeds to tests."""
    plan_ms = {
        "milestone_id": "ms-plan",
        "plan_content": "1. Implement",
        "status": "completed",
    }
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

    orch._runner = MagicMock()
    # First call (dev): timed out but produced a commit.
    # Second call (tests): succeeds with real test output.
    orch._runner.run_agent_task.side_effect = [
        _agent_result(success=False, error="Agent task timed out after 3600s"),
        _agent_result(success=True, text="2202 passed, 1 skipped"),
    ]
    # commit_before="aaa", commit_after="bbb" => sha_changed is True.
    orch._gh.get_current_commit.side_effect = ["aaa1111", "bbb2222"]

    orch._do_development(wf)

    update_calls = mock_repo.update_workflow.call_args_list
    fields_list = [c[0][1] for c in update_calls if len(c[0]) > 1 and isinstance(c[0][1], dict)]

    # No "Development failed" status update was emitted.
    assert not any(
        f.get("status") == "failed" and "Development failed" in (f.get("error_message") or "")
        for f in fields_list
    ), "dev failure should have been salvaged, not marked failed"

    # The dev milestone was marked completed (not failed) despite the timeout.
    ms_updates = mock_repo.update_milestone.call_args_list
    ms_fields = [c[0][1] for c in ms_updates if len(c[0]) > 1 and isinstance(c[0][1], dict)]
    dev_ms_updates = [f for f in ms_fields if f.get("status") == "completed"]
    assert dev_ms_updates, "dev milestone should be completed when salvaged"
    salvaged_update = dev_ms_updates[0]
    assert salvaged_update.get("error_message", "").startswith("Salvaged")


# ── Salvage (negative): failure + no commit -> still failed ───────────


def test_dev_not_salvaged_when_no_commit():
    """Without a new commit, a failed agent run must still fail the workflow."""
    plan_ms = {
        "milestone_id": "ms-plan",
        "plan_content": "1. Implement",
        "status": "completed",
    }
    wf = _make_workflow()
    orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

    orch._runner = MagicMock()
    orch._runner.run_agent_task.side_effect = [
        _agent_result(success=False, error="Agent task timed out after 3600s"),
        _agent_result(success=False, error="Tests failed"),
    ]
    # commit unchanged => sha_changed is False.
    orch._gh.get_current_commit.side_effect = ["abc1234", "abc1234"]

    orch._do_development(wf)

    update_calls = mock_repo.update_workflow.call_args_list
    fields_list = [c[0][1] for c in update_calls if len(c[0]) > 1 and isinstance(c[0][1], dict)]
    assert any(
        f.get("status") == "failed" and "Development failed" in (f.get("error_message") or "")
        for f in fields_list
    ), "dev failure with no commit should still mark the workflow failed"
