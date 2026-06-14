"""
Regression tests for prompt/display truncation redesign (Issue #987).

Verifies that:
  - pr_review round-2 ``review_prompt`` no longer re-injects content already
    carried by the review session's ``--resume`` history (requirements_text
    and the previous round's review content), while keeping the per-point
    confirmation instruction.
  - ``summary_prompt`` keeps the ``last_pr_review`` cross-session bridge
    (review runs on the review session, summary on the main session) but
    drops the dead ``last_fix_summary`` injection.
  - the progress report posted to the GitHub issue keeps full plan/test
    content (no [:300] / [:200] truncation).
"""

import logging
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import GITHUB_COMMENT_MAX_CHARS

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_workflow(**overrides):
    """Minimal workflow dict; round=2/max=2 by default so summary runs."""
    base = {
        "workflow_id": "test-wf-987",
        "title": "Test 987",
        "requirements_text": "REQ_MARKER 需要完整保留的需求描述 " * 8,
        "branch_name": "feature-x",
        "github_issue_number": 100,
        "github_pr_number": 42,
        "current_round": 1,  # round_num = current_round + 1 = 2
        "max_pr_review_rounds": 2,  # round(2) >= max(2) -> summary runs
        "dev_round": 1,
        "cli_tool": "claude-code",
        "model": "",
        "worktree_path": "/tmp/wf987",
        "project_path": "/tmp/wf987",
        "workspace_type": "local",
        "remote_machine_id": "",
        "permission_mode": "auto-edit",
    }
    base.update(overrides)
    return base


def _make_agent_result(text="代码审查通过"):
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=10,
        total_input_tokens=5,
        total_output_tokens=5,
        success=True,
        error=None,
    )


def _make_gh():
    gh = MagicMock()
    gh.get_diff_stats.return_value = {
        "commits": 1,
        "additions": 5,
        "deletions": 1,
        "files": 1,
    }
    gh.git_push.return_value = None
    gh.get_pr_diff.return_value = "FAKE_DIFF"
    gh.add_pr_comment.return_value = {}
    gh.add_issue_comment.return_value = {}
    return gh


def _make_orchestrator(wf):
    """AutonomousOrchestrator with all external deps mocked."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        orch = AutonomousOrchestrator(wf["workflow_id"])
        orch.repo = mock_repo

    orch.emitter = MagicMock()
    orch._get_gh = MagicMock()
    orch._poll_ci_status = MagicMock(return_value=[])
    orch._smart_truncate_diff = MagicMock(return_value="DIFF_TEXT")
    orch._clean_agent_text = MagicMock(side_effect=lambda x: x or "")
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-x"})
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._emit = MagicMock()
    return orch


def _capture_prompts(orch):
    """Patch _run_agent to record each prompt; returns the capture list."""
    captured = []

    def fake_run_agent(**kwargs):
        captured.append(kwargs.get("prompt", ""))
        return _make_agent_result()

    orch._run_agent = MagicMock(side_effect=fake_run_agent)
    return captured


# ── pr_review review_prompt ──────────────────────────────────────────────


class TestPrReviewPrompt:
    """Round-2 review_prompt must rely on resume, not truncated re-injection."""

    def test_requirements_not_injected(self):
        wf = _make_workflow()
        orch = _make_orchestrator(wf)
        orch._get_gh.return_value = _make_gh()
        orch.repo.list_milestones.return_value = []
        captured = _capture_prompts(orch)

        orch._do_pr_review(wf)

        # requirements_text was injected as [:500] before; now removed — the
        # review session already has full requirements from the plan_review round.
        assert "REQ_MARKER" not in captured[0]

    def test_previous_review_content_not_reinjected_but_instruction_kept(self):
        wf = _make_workflow()
        orch = _make_orchestrator(wf)
        orch._get_gh.return_value = _make_gh()
        prev_review = "PREV_REVIEW_MARKER 上一轮审查意见详细内容 " * 20
        orch.repo.list_milestones.return_value = [
            {"milestone_type": "pr_reviewed", "review_content": prev_review},
        ]
        captured = _capture_prompts(orch)

        orch._do_pr_review(wf)

        review_prompt = captured[0]
        # previous review was re-injected as [:3000] before; now removed (resume carries it)
        assert "PREV_REVIEW_MARKER" not in review_prompt
        # the per-point confirmation instruction is retained
        assert "逐条确认" in review_prompt


# ── summary_prompt ───────────────────────────────────────────────────────


class TestSummaryPrompt:
    """summary_prompt keeps last_pr_review (cross-session bridge) but drops
    the dead last_fix_summary injection."""

    def test_last_pr_review_kept_last_fix_summary_removed(self):
        wf = _make_workflow()  # round=2 >= max=2 -> summary runs
        orch = _make_orchestrator(wf)
        orch._get_gh.return_value = _make_gh()
        last_review = "LAST_REVIEW_MARKER 最后一轮审查意见 " * 10
        orch.repo.list_milestones.return_value = [
            {"milestone_type": "pr_reviewed", "review_content": last_review},
        ]
        captured = _capture_prompts(orch)

        orch._do_pr_review(wf)

        # captured[0] = review prompt, captured[1] = summary prompt
        assert len(captured) >= 2
        summary_prompt = captured[1]
        # dead last_fix_summary section removed (pr_updated never writes
        # result_summary, so it was always empty; and fix runs on main session)
        assert "最后一次修复记录" not in summary_prompt
        # last_pr_review cross-session bridge retained (review session != main)
        assert "LAST_REVIEW_MARKER" in summary_prompt


# ── report (GitHub issue comment) ────────────────────────────────────────


class TestReportNotTruncated:
    """Progress report posted to the GitHub issue keeps full plan/test content."""

    def test_plan_and_test_sections_full(self):
        wf = _make_workflow(current_round=0, max_pr_review_rounds=1)
        orch = _make_orchestrator(wf)
        gh = _make_gh()
        orch._get_gh.return_value = gh

        long_plan = "# PLAN_MARKER\n" + "方案详细内容行内容\n" * 60
        long_test = "TEST_MARKER 测试输出详情\n" + "passed case line\n" * 60
        orch.repo.list_milestones.return_value = [
            {
                "phase": "planning",
                "milestone_type": "plan_finalized",
                "plan_content": long_plan,
            },
            {"milestone_type": "tests_run", "result_summary": long_test},
        ]

        captured = {}

        def fake_comment(num, body):
            captured["comment"] = body
            return {}

        gh.add_issue_comment.side_effect = fake_comment

        orch._do_report(wf)

        comment = captured["comment"]
        # plan section not truncated at [:300]
        assert "PLAN_MARKER" in comment
        assert "方案详细内容行内容" in comment
        # test section not truncated at [:200] (nor the writer-side [:300])
        assert "TEST_MARKER" in comment
        assert "passed case line" in comment


# ── _post_github_comment (finding 1: GitHub hard-limit + silent failure) ──


class TestPostGithubComment:
    """``_post_github_comment`` caps over-long bodies and logs failures instead
    of swallowing them — GitHub rejects comment bodies > 65536 chars, and the
    old ``try/except: pass`` around every post made a rejected comment vanish
    with no trace."""

    def test_short_body_posted_verbatim(self):
        orch = _make_orchestrator(_make_workflow())
        gh = MagicMock()
        orch._post_github_comment(gh, 42, "short body", context="t")
        gh.add_issue_comment.assert_called_once_with(42, "short body")
        gh.add_pr_comment.assert_not_called()

    def test_is_pr_routes_to_pr_comment(self):
        orch = _make_orchestrator(_make_workflow())
        gh = MagicMock()
        orch._post_github_comment(gh, 7, "x", is_pr=True)
        gh.add_pr_comment.assert_called_once_with(7, "x")
        gh.add_issue_comment.assert_not_called()

    def test_long_body_capped_with_notice(self):
        orch = _make_orchestrator(_make_workflow())
        gh = MagicMock()
        long_body = "B" * (GITHUB_COMMENT_MAX_CHARS + 5000)
        orch._post_github_comment(gh, 42, long_body, context="report")
        posted = gh.add_issue_comment.call_args[0][1]
        # capped body respects the GitHub limit
        assert len(posted) <= GITHUB_COMMENT_MAX_CHARS
        # truncation notice points readers to the timeline full-text view
        assert "截断" in posted
        # head of the content is preserved
        assert posted.startswith("B")

    def test_failure_is_logged_not_raised(self, caplog):
        orch = _make_orchestrator(_make_workflow())
        gh = MagicMock()
        gh.add_issue_comment.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.WARNING):
            orch._post_github_comment(gh, 42, "body", context="report")  # must not raise
        # failure logged with the context + issue number so it's diagnosable
        assert any("report" in rec.message and "#42" in rec.message for rec in caplog.records)
