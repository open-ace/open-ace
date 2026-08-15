"""#2699: lazy dependency preparation for agent worktrees.

The reverted node_modules shim (#2694/#2698) left the cross-user EACCES trap
in place: a clean worktree has no ``node_modules`` (gitignored), Node
resolution walks up into the project owner's main clone — deps load (reads
work) but vite's cache write into the owner-owned ``node_modules/.vite-temp``
is denied. To the agent that reads like a permissions bug, not missing deps,
so it retries around permissions instead of installing.

These tests lock the two replacement layers:

1. Proactive rule in the test-phase strategy prompt (check node_modules +
   lockfile-detected install before running frontend commands).
2. Failure-signature translation: the prior round's report hitting the
   missing-deps signature injects an explicit "install deps first" directive
   into (a) the fresh-retry prompt and (b) the #2590 dev-repair feedback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    _DEPENDENCY_PREP_RULE,
    _dependency_failure_directive,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_PY = PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "orchestrator.py"

pytestmark = [pytest.mark.issue(2699), pytest.mark.regression]


class TestDependencyFailureDirective:
    def test_matches_vite_temp_eacces(self):
        out = (
            "Error: EACCES: permission denied, open "
            "'/home/qlfan/repo/frontend/node_modules/.vite-temp/config.json'"
        )
        directive = _dependency_failure_directive(out)
        assert directive, "EACCES on node_modules/.vite-temp must trigger the directive"
        assert "npm ci" in directive
        assert "pnpm install --frozen-lockfile" in directive

    def test_matches_cannot_find_module_vitest(self):
        out = "node:internal/modules/cjs/loader: Error: Cannot find module 'vitest'"
        assert _dependency_failure_directive(out)

    def test_matches_windows_style_path(self):
        out = r"Error: EACCES: permission denied 'C:\repo\frontend\node_modules\.vite-temp\x'"
        assert _dependency_failure_directive(out)

    def test_normal_pytest_failure_no_directive(self):
        out = "FAILED tests/test_app.py::test_login - AssertionError: expected 200"
        assert _dependency_failure_directive(out) == ""

    def test_empty_output_no_directive(self):
        assert _dependency_failure_directive("") == ""
        assert _dependency_failure_directive(None) == ""  # type: ignore[arg-type]

    def test_directive_is_actionable_not_permission_chasing(self):
        """The directive must redirect the agent away from permission
        workarounds (sudo/chmod) toward installing deps in the worktree."""
        directive = _dependency_failure_directive(
            "EACCES: permission denied, open '/r/frontend/node_modules/.vite-temp/c'"
        )
        assert "worktree" in directive
        assert "重新执行" in directive


class TestProactiveRule:
    def test_rule_covers_all_four_lockfiles(self):
        for cmd in (
            "npm ci",
            "pnpm install --frozen-lockfile",
            "yarn install --frozen-lockfile",
            "bun install",
        ):
            assert cmd in _DEPENDENCY_PREP_RULE, cmd

    def test_rule_scopes_to_frontend_commands(self):
        """Pure-backend tasks must stay zero-cost — the rule must tell the
        agent it only applies before frontend/JS commands."""
        assert "前端" in _DEPENDENCY_PREP_RULE
        assert "node_modules" in _DEPENDENCY_PREP_RULE

    @pytest.mark.issue(2699)
    def test_rule_wired_into_test_prompt(self):
        """Drift lock: the test-phase strategy prompt must reference the rule
        constant (mirroring the #2635/#2674 source-grep locks)."""
        text = ORCHESTRATOR_PY.read_text()
        assert "_DEPENDENCY_PREP_RULE" in text, (
            "orchestrator no longer references _DEPENDENCY_PREP_RULE; the "
            "proactive dependency rule must stay wired into the test-phase "
            "strategy prompt (Issue #2699)"
        )

    def test_directive_wired_into_retry_and_repair_paths(self):
        """Drift lock: the signature translation must be consulted at both
        delivery sites — the fresh-retry prompt (prior milestone summary) and
        the #2590 dev-repair feedback. Pins the exact call shapes: a bare
        substring count would also match the _prior_ helper's own name."""
        text = ORCHESTRATOR_PY.read_text()
        # Retry-prompt site (method call; the `def` line spells it without the
        # `self.` prefix, so this only matches the call).
        assert "self._prior_dependency_failure_directive(" in text
        # Dev-repair feedback site (module-level call with the report text).
        assert '_dependency_failure_directive(test_summary or "")' in text


class TestPriorSummaryDirective:
    def test_orchestrator_helper_reads_prior_tests_run_summary(self):
        """The retry-path helper must read the PRIOR tests_run milestone's
        result_summary (excluding the current milestone) — same milestone
        selection as _carry_forward_prior_test_verdict."""
        text = ORCHESTRATOR_PY.read_text()
        assert "_prior_dependency_failure_directive" in text
        # Exclusion of the current milestone is load-bearing: without it the
        # just-created in_progress tests_run milestone (empty summary) could
        # shadow the prior round's report.
        assert "!= current_milestone_id" in text


class TestRetryPromptDelivery:
    """Behavioral: drive ``_run_test_phase`` (harness mirrors
    tests/unit/test_prior_milestone_verdict_carryforward.py) and assert the
    directive actually lands in the dispatched agent prompt — not merely that
    the source references it."""

    @staticmethod
    def _drive(test_retries: int, prior_summary: str):
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = {
            "workflow_id": "wf-2699",
            "user_id": 1,
            "title": "T",
            "status": "developing",
            "requirements_text": "r",
            "project_path": "/tmp/p",
            "worktree_path": "/tmp/p",
            "workspace_type": "local",
            "cli_tool": "claude-code",
            "branch_name": "auto-dev/x",
            "branch_strategy": "worktree",
            "current_phase": "development",
            "dev_round": 1,
            "current_round": 1,
            "github_issue_number": 2699,
            "test_retries": test_retries,
            "skip_retries": 0,
            "dev_retries_on_test_fail": 0,
            "error_message": "",
        }
        result = AgentTaskResult(
            session_id="sess",
            response_text="1 passed",
            visible_response_text="1 passed",
            success=True,
        )
        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator." "AutonomousWorkflowRepository"
            ) as repo_cls,
        ):
            repo = MagicMock()
            repo.get_workflow.return_value = wf
            repo.list_milestones.return_value = [
                {
                    "milestone_id": "ms-prior",
                    "workflow_id": "wf-2699",
                    "phase": "development",
                    "milestone_type": "tests_run",
                    "id": 10,
                    "result_summary": prior_summary,
                },
                {
                    "milestone_id": "ms-cur",
                    "workflow_id": "wf-2699",
                    "phase": "development",
                    "milestone_type": "tests_run",
                    "id": 11,
                    "result_summary": "",
                },
            ]
            repo.create_milestone.return_value = {"milestone_id": "ms-cur"}
            repo.update_workflow.return_value = wf
            repo.update_milestone.return_value = {}
            repo.create_event.return_value = {"id": 1}
            repo_cls.return_value = repo
            from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

            orch = AutonomousOrchestrator("wf-2699")
            orch.repo = repo
            orch.emitter = MagicMock()
            orch._gh = MagicMock()
            orch._gh.has_uncommitted_changes.return_value = False
        orch._update_workflow = MagicMock()
        orch._post_github_comment = MagicMock()
        orch._run_agent = MagicMock(return_value=result)
        orch._runtime_environment_gate = MagicMock(return_value="")
        orch._build_test_execution_context = MagicMock(return_value=("", []))
        orch._project_runtime_contract = MagicMock(return_value="")
        orch._artifact_visible_text = MagicMock(return_value="1 passed")
        orch._artifact_text = MagicMock(return_value="1 passed")
        orch._artifact_tldr = MagicMock(return_value="")
        orch._shadow_compare_evidence = MagicMock()
        orch._validate_test_report_format = MagicMock(return_value=(True, ""))
        structured = (
            "app.modules.workspace.autonomous.orchestrator."
            "AutonomousOrchestrator._compute_structured_test_verdict"
        )
        passing = "app.modules.workspace.autonomous.orchestrator." "_has_passing_test_tool_result"
        with (
            patch(structured, return_value=("passed", [], "scripted")),
            patch(passing, return_value=True),
        ):
            orch._run_test_phase(wf, 1, orch._gh)
        return orch

    def test_retry_prompt_contains_directive_when_prior_report_hits_signature(self):
        orch = self._drive(
            test_retries=1,
            prior_summary=(
                "Error: EACCES: permission denied, open "
                "'/r/frontend/node_modules/.vite-temp/config.json'"
            ),
        )
        prompt = orch._run_agent.call_args.kwargs["prompt"]
        assert "依赖环境修复指令" in prompt
        assert "npm ci" in prompt

    def test_retry_prompt_clean_prior_report_no_directive(self):
        orch = self._drive(
            test_retries=1,
            prior_summary="2 passed, 0 failed (pytest)",
        )
        prompt = orch._run_agent.call_args.kwargs["prompt"]
        assert "依赖环境修复指令" not in prompt

    def test_first_round_never_gets_directive_even_with_stale_summary(self):
        """No retry → no directive, regardless of what an old milestone says —
        the proactive rule (item 5) is the only first-round guidance."""
        orch = self._drive(
            test_retries=0,
            prior_summary="EACCES: permission denied node_modules/.vite-temp",
        )
        prompt = orch._run_agent.call_args.kwargs["prompt"]
        assert "依赖环境修复指令" not in prompt
        # The proactive rule IS present on every round.
        assert "npm ci" in prompt
