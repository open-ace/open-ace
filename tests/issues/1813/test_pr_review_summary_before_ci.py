"""Tests for pr_review_summary created before CI check (issue #1813).

When PR review passes but CI fails, the workflow enters the CI repair loop
and returns early. Previously the ``pr_review_summary`` milestone's agent run
+ ``review_content`` fill happened AFTER the CI check — so when CI failed,
the summary was never generated (empty ``review_content``), and the frontend
"PR Review Summary" button stayed permanently disabled.

The fix moves the ENTIRE summary block (create → run agent → fill
review_content → post comment) BEFORE the CI check.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1813",
        "user_id": 1,
        "title": "Test",
        "status": "pr_review",
        "requirements_text": "Build feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/p",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "glm-5",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/x",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/p",
        "github_issue_number": 1813,
        "github_pr_number": 99,
        "github_pr_url": "",
        "current_phase": "pr_review",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 3,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "content_language": "zh",
    }
    base.update(overrides)
    return base


class TestPrReviewSummaryBeforeCiCheck:
    """The ENTIRE summary block (create → agent → review_content → completed)
    must run BEFORE the CI failure check. When CI fails, the summary must
    still be completed with non-empty review_content — otherwise the frontend
    "PR Review Summary" button stays disabled (#1813)."""

    def test_review_content_filled_before_ci_return(self):
        """When review passes but CI fails, the pr_review_summary milestone
        must be updated with non-empty review_content AND status=completed
        BEFORE the CI failure path returns.

        We mock the agent runner to return summary text, then verify
        update_milestone was called with review_content before
        _start_ci_repair_round."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        wf = _make_workflow()
        call_order = []  # track method call order

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf
            mock_repo.list_milestones.return_value = []

            # Return a distinct milestone_id per type so we can filter
            # update_milestone calls by the SUMMARY milestone only.
            def fake_create(kwargs):
                return {
                    "milestone_id": f"ms-{kwargs.get('milestone_type', '')}",
                    "workflow_id": "wf-1813",
                }

            mock_repo.create_milestone.side_effect = fake_create
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf

            # Track update_milestone calls WITH ms_id to distinguish milestones.
            def track_update(ms_id, fields):
                call_order.append(("update_milestone", ms_id, dict(fields)))

            mock_repo.update_milestone.side_effect = track_update
            mock_repo.update_workflow_tokens.return_value = None
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator("wf-1813")
            orch.repo = mock_repo
            orch.emitter = MagicMock()
            # Bypass _emit's json.dumps to avoid MagicMock serialization issues.
            orch._emit = MagicMock()
            orch._accumulate_tokens = MagicMock()
            orch._gh = MagicMock()
            orch._gh.get_current_commit.return_value = "abc"
            orch._gh.get_current_branch.return_value = "auto-dev/x"
            orch._gh.get_pr_head_sha.return_value = "abc"
            orch._gh.get_diff.return_value = "diff content"
            orch._gh.get_diff_stats.return_value = {
                "additions": 10,
                "deletions": 2,
                "files": 3,
                "commits": 1,
            }
            orch._gh.create_pr.return_value = {
                "number": 99,
                "url": "https://github.com/pull/99",
            }
            orch._post_github_comment = MagicMock()
            # Skip the PR review fix path — we only care about the summary
            # ordering relative to the CI check, not the fix mechanics.
            orch._apply_pr_review_fix = MagicMock()

            orch._runner = MagicMock()
            orch._runner.run_agent_task.return_value = AgentTaskResult(
                session_id="s",
                response_text=(
                    '代码审查通过\nREVIEW_RESULT: {"verdict":"APPROVE",' '"blocking_findings":[]}'
                ),
                visible_response_text=(
                    '代码审查通过\nREVIEW_RESULT: {"verdict":"APPROVE",' '"blocking_findings":[]}'
                ),
                success=True,
            )

            # Track CI repair call order.
            def track_ci_repair(*a, **kw):
                call_order.append(("ci_repair",))

            with (
                patch.object(
                    orch, "_poll_ci_status", return_value=[{"name": "lint", "bucket": "fail"}]
                ),
                patch.object(orch, "_start_ci_repair_round", side_effect=track_ci_repair),
            ):
                orch._do_pr_review(wf)

        # Find the update_milestone call for pr_review_summary (by id) that
        # set review_content. Using ms_id avoids false-positives from
        # pr_reviewed milestones which also carry review_content.
        summary_updates = [
            c
            for c in call_order
            if c[0] == "update_milestone"
            and c[1] == "ms-pr_review_summary"
            and c[2].get("review_content")
        ]
        assert summary_updates, (
            "pr_review_summary must be updated with non-empty review_content " "even when CI fails"
        )
        assert summary_updates[0][2]["review_content"].strip(), "review_content must be non-empty"
        assert summary_updates[0][2]["status"] == "completed", "pr_review_summary must be completed"

        # Verify review_content update happened BEFORE ci_repair.
        summary_idx = call_order.index(summary_updates[0])
        ci_repair_calls = [i for i, c in enumerate(call_order) if c[0] == "ci_repair"]
        assert ci_repair_calls, "ci_repair should have been called"
        assert (
            summary_idx < ci_repair_calls[0]
        ), "review_content must be filled BEFORE entering CI repair"

    def test_summary_overflow_fails_workflow_instead_of_entering_report(self):
        """A terminal 400 summary is not valid review content or a success."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        wf = _make_workflow()
        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf
            mock_repo.list_milestones.return_value = []
            mock_repo.create_milestone.side_effect = lambda fields: {
                "milestone_id": f"ms-{fields.get('milestone_type')}",
                "workflow_id": wf["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf["workflow_id"])
            orch.repo = mock_repo
            orch._emit = MagicMock()
            orch._accumulate_tokens = MagicMock()
            orch._post_github_comment = MagicMock()
            orch._gh = MagicMock()
            orch._gh.get_pr_diff.return_value = "diff"
            orch._gh.get_diff_stats.return_value = {"commits": 1}
            orch._gh.get_current_branch.return_value = wf["branch_name"]
            orch._gh.get_current_commit.return_value = "branch-sha"

            def run_git(args, check=True):
                if args[:1] == ["rev-parse"]:
                    return MagicMock(stdout=f"{args[1]}-sha\n", returncode=0)
                if args[:2] == ["merge-base", "--is-ancestor"]:
                    return MagicMock(stdout="", returncode=1)
                return MagicMock(stdout="", returncode=0)

            orch._gh._run_git.side_effect = run_git
            orch._validate_autonomous_change_scope = MagicMock(return_value="")
            approved = AgentTaskResult(
                session_id="review",
                success=True,
                response_text=(
                    '代码审查通过\nREVIEW_RESULT: {"verdict":"APPROVE",' '"blocking_findings":[]}'
                ),
            )
            overflow = AgentTaskResult(
                session_id="main-replacement",
                success=True,
                response_text=(
                    "API Error: 400 InternalError.Algo.InvalidParameter: "
                    "Range of input length should be [1, 202752]"
                ),
            )
            orch._run_agent_with_context_recovery = MagicMock(side_effect=[approved, overflow])

            with patch.object(orch, "_poll_ci_status", return_value=[]):
                orch._do_pr_review(wf)

        workflow_updates = [call.args[1] for call in mock_repo.update_workflow.call_args_list]
        assert any(update.get("status") == "failed" for update in workflow_updates)
        assert not any(update.get("status") == "reporting" for update in workflow_updates)
        summary_updates = [
            call.args[1]
            for call in mock_repo.update_milestone.call_args_list
            if call.args[0] == "ms-pr_review_summary"
        ]
        assert summary_updates[-1]["status"] == "failed"
        assert summary_updates[-1]["review_content"] == ""
