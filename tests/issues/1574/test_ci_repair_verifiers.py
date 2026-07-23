"""Regression tests for evidence-driven CI repair flow (Issue #1574)."""

from unittest.mock import MagicMock, patch


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1574",
        "user_id": 1,
        "title": "issue-1574",
        "status": "developing",
        "requirements_text": "Fix CI",
        "requirements_issue_url": "",
        "project_path": "/tmp/repo",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-1574",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "github_issue_number": 1574,
        "github_pr_number": 1697,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 0,
        "dev_round": 2,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "base_commit_sha": "base-sha",
        "last_ci_failure_head_sha": "",
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
        orch._sync_failed_pr_with_main = MagicMock(return_value=False)
    return orch, mock_repo


def test_build_ci_repair_context_includes_failure_excerpt_and_investigation_steps():
    wf = _make_workflow(project_path="/tmp/repo", worktree_path="/tmp/repo")
    orch, _ = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_check_failure_excerpt.return_value = "black failed\nfiles were modified"

    context = orch._build_ci_repair_context(
        wf,
        gh,
        1697,
        [{"name": "lint", "state": "failure", "bucket": "fail", "link": "https://example.com"}],
    )

    assert "black failed" in context
    assert ".github/workflows/" in context
    assert "package.json" in context
    assert "不能只跑“相关测试”就宣布已解决" in context


def test_do_development_skips_test_phase_when_dev_failed():
    wf = _make_workflow()
    failed_wf = dict(wf, status="failed", error_message="Development failed")
    orch, mock_repo = _make_orchestrator(wf)
    mock_repo.get_workflow.return_value = wf

    def fail_dev(*_args, **_kwargs):
        mock_repo.get_workflow.return_value = failed_wf

    orch._get_gh = MagicMock(return_value=MagicMock())
    orch._run_development_agent = MagicMock(side_effect=fail_dev)
    orch._post_dev_completion_comment = MagicMock()
    orch._run_test_phase = MagicMock()

    orch._do_development(wf)

    orch._post_dev_completion_comment.assert_not_called()
    orch._run_test_phase.assert_not_called()


def test_start_ci_repair_round_stays_in_merge_and_runs_merge_repair():
    wf = _make_workflow(status="merging", current_phase="merge", ci_repair_attempts=0)
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-old"
    gh.get_check_failure_excerpt.return_value = "black failed\nfiles were modified"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    failed_checks = [
        {"name": "lint", "state": "failure", "bucket": "fail", "link": "https://example.com"}
    ]
    orch._start_ci_repair_round(wf, 1697, failed_checks)

    orch._run_merge_ci_repair.assert_called_once()
    repair_args = orch._run_merge_ci_repair.call_args.args
    assert repair_args[:3] == (wf, gh, 1697)
    assert repair_args[3][0]["failure_excerpt"] == "black failed\nfiles were modified"
    updates = mock_repo.update_workflow.call_args.args[1]
    assert updates["current_phase"] == "merge"
    assert updates["status"] == "merging"
    assert updates["ci_repair_attempts"] == 1
    assert updates["last_ci_failure_head_sha"] == "sha-old"
    assert "dev_round" not in updates
    assert "current_round" not in updates


def test_start_ci_repair_round_fails_when_signature_unchanged_after_new_head():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    failed_checks = [
        {"name": "lint", "state": "failure", "bucket": "fail", "link": "https://example.com"}
    ]
    # Use the new fine-grained fingerprint format (lint::<digest>). The digest
    # must match what _ci_failure_fingerprint produces for the same excerpt.
    # We mock get_check_failure_excerpt so the fingerprint is deterministic.
    import hashlib

    excerpt = "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
    expected_digest = hashlib.sha256(
        AutonomousOrchestrator._normalize_failure_excerpt(excerpt).encode()
    ).hexdigest()[:12]
    expected_fingerprint = f"lint::{expected_digest}"

    wf = _make_workflow(
        status="merging",
        current_phase="merge",
        ci_repair_attempts=1,
        last_ci_failure_signature=expected_fingerprint,
        last_ci_failure_head_sha="sha-old",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = excerpt
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1697, failed_checks)

    orch._run_merge_ci_repair.assert_not_called()
    updates = mock_repo.update_workflow.call_args.args[1]
    assert updates["status"] == "failed"
    assert "CI 失败在自动修复后仍未变化" in updates["error_message"]


def test_run_merge_ci_repair_pushes_commit_and_stays_in_merge():
    from app.modules.workspace.autonomous.models import AgentTaskResult

    wf = _make_workflow(
        status="merging",
        current_phase="merge",
        ci_repair_attempts=1,
        ci_repair_context="### lint\n- 状态: failure",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_current_commit.side_effect = ["sha-old", "sha-new"]
    gh.get_current_branch.return_value = wf["branch_name"]
    gh.get_commit_diff_stats.return_value = {"files": 1, "additions": 3, "deletions": 1}
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(
            success=True,
            session_id="sess-1",
            response_text="Reproduced lint command and fixed formatting.",
            visible_response_text="Reproduced lint command and fixed formatting.",
            error="",
        )
    )

    orch._run_merge_ci_repair(
        wf,
        gh,
        1697,
        [{"name": "lint", "state": "failure", "bucket": "fail", "link": "https://example.com"}],
    )

    gh.git_push.assert_called_once_with(branch=wf["branch_name"], force_with_lease=True)
    updates = mock_repo.update_workflow.call_args.args[1]
    assert updates["current_phase"] == "merge"
    assert updates["status"] == "merging"
    assert updates["error_message"] == ""
    orch._post_github_comment.assert_called_once()
