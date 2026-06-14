"""Tests for the per-round TL;DR milestone summary (Issue #993).

Verifies:
  - ``_extract_tldr`` pulls the ``TL;DR: ...`` line (case-insensitive, first
    match, capped at 200 chars, empty when absent / whitespace-stripped).
  - ``TLDR_INSTRUCTION`` is appended to every ``_run_agent`` prompt.
  - phase milestones persist the extracted ``tldr`` alongside ``result_summary``.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import TLDR_INSTRUCTION, AutonomousOrchestrator

# ── helpers (mirror tests/issues/987/) ───────────────────────────────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "test-wf-993",
        "title": "Test 993",
        "requirements_text": "REQ",
        "branch_name": "feature-x",
        "github_issue_number": 100,
        "github_pr_number": 42,
        "current_round": 1,
        "max_pr_review_rounds": 2,
        "dev_round": 1,
        "cli_tool": "claude-code",
        "model": "",
        "worktree_path": "/tmp/wf993",
        "project_path": "/tmp/wf993",
        "workspace_type": "local",
        "remote_machine_id": "",
        "permission_mode": "auto-edit",
    }
    base.update(overrides)
    return base


def _make_agent_result(text="代码审查通过\nTL;DR: 修复了登录 bug"):
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


# ── _extract_tldr ────────────────────────────────────────────────────────


class TestExtractTldr:
    """_extract_tldr pulls the agent's appended TL;DR one-liner."""

    def test_extracts_tldr_line(self):
        r = AutonomousOrchestrator._extract_tldr("工作完成\nTL;DR: 实现了登录功能\n")
        assert r == "实现了登录功能"

    def test_case_insensitive(self):
        assert AutonomousOrchestrator._extract_tldr("tl;dr: lower") == "lower"
        assert AutonomousOrchestrator._extract_tldr("TL;DR: upper") == "upper"
        assert AutonomousOrchestrator._extract_tldr("Tl;Dr: mixed") == "mixed"

    def test_empty_when_absent(self):
        assert AutonomousOrchestrator._extract_tldr("no summary here") == ""

    def test_empty_on_empty_input(self):
        assert AutonomousOrchestrator._extract_tldr("") == ""

    def test_takes_first_when_multiple(self):
        r = AutonomousOrchestrator._extract_tldr("TL;DR: first\nbody\nTL;DR: second")
        assert r == "first"

    def test_truncates_over_200(self):
        r = AutonomousOrchestrator._extract_tldr("TL;DR: " + "x" * 300)
        assert len(r) == 200
        assert r == "x" * 200

    def test_strips_surrounding_whitespace(self):
        r = AutonomousOrchestrator._extract_tldr("TL;DR:   padded summary   ")
        assert r == "padded summary"


# ── TLDR_INSTRUCTION + _run_agent appending ──────────────────────────────


class TestTldrInstruction:
    """TLDR_INSTRUCTION is appended to every _run_agent prompt so each phase
    agent emits a one-line summary."""

    def test_instruction_contains_tldr_format(self):
        assert "TL;DR:" in TLDR_INSTRUCTION

    def test_run_agent_appends_instruction(self):
        orch = _make_orchestrator(_make_workflow())
        captured = {}

        def fake_run(**kwargs):
            captured["prompt"] = kwargs.get("prompt", "")
            return _make_agent_result("ok")

        orch._runner = MagicMock()
        orch._runner.run_agent_task = MagicMock(side_effect=fake_run)
        orch._runner._uses_sidebar_session_source = MagicMock(return_value=False)
        orch._resolve_session_line = MagicMock(return_value=("sess", None, False))
        orch._link_session_to_current_milestone = MagicMock()
        orch._is_rate_limited = MagicMock(return_value=False)
        orch._write_phase_usage = MagicMock()

        orch._run_agent(prompt="基础 prompt")

        # the instruction is appended to the original prompt before it reaches
        # the runner
        assert captured["prompt"].startswith("基础 prompt")
        assert TLDR_INSTRUCTION in captured["prompt"]


# ── milestone write carries tldr ──────────────────────────────────────────


class TestMilestoneTldrWrite:
    """Phase milestones persist the extracted tldr alongside result_summary."""

    def test_pr_reviewed_milestone_carries_tldr(self):
        wf = _make_workflow(current_round=0, max_pr_review_rounds=1)  # review-only round
        orch = _make_orchestrator(wf)
        orch._get_gh.return_value = _make_gh()
        orch.repo.list_milestones.return_value = []
        review_text = "代码审查完成，发现 2 个小问题但非阻塞\nTL;DR: 审查通过，可合并"
        orch._run_agent = MagicMock(return_value=_make_agent_result(review_text))

        orch._do_pr_review(wf)

        # find the pr_reviewed update_milestone call (the one carrying review_content)
        review_updates = [
            call.args[1]
            for call in orch.repo.update_milestone.call_args_list
            if len(call.args) > 1
            and isinstance(call.args[1], dict)
            and "review_content" in call.args[1]
        ]
        assert review_updates, "pr_reviewed milestone update not captured"
        # tldr extracted from the agent response, result_summary still the [:200] slice
        assert review_updates[0]["tldr"] == "审查通过，可合并"
        assert review_updates[0]["review_content"] == review_text
