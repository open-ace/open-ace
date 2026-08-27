"""Tests for the per-round TL;DR milestone summary (Issue #993).

Verifies:
  - ``_extract_tldr`` pulls the ``TL;DR: ...`` line (case-insensitive, first
    match, capped at 200 chars, empty when absent / whitespace-stripped).
  - ``TLDR_INSTRUCTION`` is appended to every ``_run_agent`` prompt.
  - phase milestones persist the extracted ``tldr`` alongside ``result_summary``.

Migrated from tests/issues/993/test_tldr.py by batch 16 (#2429); straight
move, no body changes.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import TLDR_INSTRUCTION, AutonomousOrchestrator

pytestmark = [pytest.mark.regression, pytest.mark.issue(993)]

# ── helpers (mirror tests/issues/987/) ───────────────────────────────────

FEATURE_SHA = "f" * 40
MAIN_SHA = "m" * 40


class _GitResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_run_git(args, check=True):
    """#2457 realignment: pr_review.handle() shells out via gh._run_git to
    detect branch state before any milestone is written. A bare MagicMock
    makes stdout.strip() truthy and the REAL _validate_autonomous_change_scope
    then fails closed on merge-base derivation, returning before the review
    agent ever runs. Give the subprocess results concrete shapes so the real
    scope validator executes and passes:
      - rev-parse feature-x → FEATURE_SHA, rev-parse main → MAIN_SHA
      - merge-base --is-ancestor → rc!=0 (branch is NOT behind main)
      - rev-parse SHA^2 → rc!=0 (head is not a merge commit)
      - merge-base ... origin/main → rc!=0 (derivation fails; the pinned
        base_commit_sha stays the scope base)
    """
    cmd = args[0] if args else ""
    if cmd == "rev-parse":
        target = args[1] if len(args) > 1 else ""
        if target == "main":
            return _GitResult(stdout=MAIN_SHA + "\n")
        if target.endswith("^2"):
            return _GitResult(returncode=128)
        return _GitResult(stdout=FEATURE_SHA + "\n")
    if cmd == "merge-base":
        return _GitResult(returncode=1)
    return _GitResult(stdout="")


def _trusted_repo_context(orch):
    """Satisfy the pre-agent trusted-git boundary (#2457 realignment).

    _run_agent refuses to execute local agents without a trusted repo context
    (repo_integrity_violation), so the MagicMock gh can no longer carry a
    direct _run_agent call into the runner. Provide the regular-repo context
    shape the 723/826 tests use.
    """
    orch._snapshot_repo_context = MagicMock(
        return_value={
            "context": {"repo_path": "/tmp/wf993", "expected_branch": "feature-x"},
            "effective": {
                "repo_path": "/tmp/wf993",
                "git_dir": "/tmp/wf993/.git",
                "git_identity": "test-git",
                "common_dir": "/tmp/wf993/.git",
                "common_identity": "test-common",
                "origin": "",
            },
            "main": {},
        }
    )
    orch._validate_repo_context_after_run = MagicMock(return_value="")


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
        "content_language": "zh",
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
    gh._run_git = MagicMock(side_effect=_fake_run_git)
    gh.get_current_branch.return_value = "feature-x"
    gh.get_changed_files.return_value = []
    gh.has_uncommitted_changes.return_value = False
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
    # Fix commits persist the resulting SHA + per-commit diff stats; stub both
    # so the pr_updated milestone write doesn't try to json-serialize a mock.
    gh.get_current_commit.return_value = "abc1234"
    gh.get_commit_diff_stats.return_value = {
        "additions": 5,
        "deletions": 1,
        "files": 1,
    }
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
        orch._is_transient_api_error = MagicMock(return_value=False)
        orch._write_phase_usage = MagicMock()
        _trusted_repo_context(orch)

        orch._run_agent(prompt="基础 prompt")

        # the instruction is appended to the original prompt before it reaches
        # the runner
        assert captured["prompt"].startswith("基础 prompt")
        assert TLDR_INSTRUCTION in captured["prompt"]


# ── milestone write carries tldr ──────────────────────────────────────────


class TestMilestoneTldrWrite:
    """Phase milestones persist the extracted tldr alongside result_summary."""

    def test_pr_reviewed_milestone_carries_tldr(self):
        wf = _make_workflow(
            current_round=0,
            max_pr_review_rounds=1,  # review-only round
            # Scope validation compares against base_commit_sha; pin it to the
            # branch head so only the current-round range is checked.
            base_commit_sha=FEATURE_SHA,
        )
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
