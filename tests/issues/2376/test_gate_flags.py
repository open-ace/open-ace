"""Gate routing under PASSED-only authority (#2376 PR-2, D2).

These drive ``_run_test_phase`` itself, because the D2 half of #2376 is *gate*
behaviour, not verdict behaviour — an assertion that merely restates
``verdict == PASSED`` is a tautology that passes unmodified on main.

The routing the tests pin:

    structured PASSED   -> authoritative, proceeds
    structured FAILED   -> NOT authoritative; only a conclusive tool-result pass
                           may override it, never agent prose (#1967)
    NOT_RUN/INCONCLUSIVE-> legacy heuristic, prose fallback still allowed

The prose exclusion is the load-bearing part. ``tests_actually_run`` is not
``_has_passing_test_tool_result`` alone — it ORs in the #1830 prose fallback,
whose regex ``\\b[1-9]\\d*\\s+passed\\b`` is satisfied by the most common
partial failure ("1 failed, 243 passed"). Without the exclusion a structured
FAILED walks straight into pr_review on the agent's own summary.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict
from app.modules.workspace.autonomous.models import AgentTaskResult

_STRUCTURED = (
    "app.modules.workspace.autonomous.orchestrator."
    "AutonomousOrchestrator._compute_structured_test_verdict"
)
_PASSING_TOOL = "app.modules.workspace.autonomous.orchestrator._has_passing_test_tool_result"


def _workflow(**overrides):
    base = {
        "workflow_id": "wf-2376",
        "user_id": 1,
        "title": "T",
        "status": "developing",
        "requirements_text": "r",
        "project_path": "/tmp/p",
        "worktree_path": "/tmp/p",
        "workspace_type": "local",
        "cli_tool": "claude-code",
        "branch_name": "auto-dev/x",
        "branch_strategy": "new-branch",
        "current_phase": "development",
        "dev_round": 1,
        "current_round": 1,
        "github_issue_number": 4321,
        "test_retries": 0,
        "skip_retries": 0,
        "dev_retries_on_test_fail": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _orchestrator(wf):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as repo_cls,
    ):
        repo = MagicMock()
        repo.get_workflow.return_value = wf
        repo.list_milestones.return_value = []
        repo.create_milestone.return_value = {"milestone_id": "ms-1", "workflow_id": "wf-2376"}
        repo.update_workflow.return_value = wf
        repo.update_milestone.return_value = {}
        repo.create_event.return_value = {"id": 1}
        repo_cls.return_value = repo
        orch = AutonomousOrchestrator("wf-2376")
        orch.repo = repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.has_uncommitted_changes.return_value = False
        return orch, repo


def _run(orch, wf, *, verdict, text, tool_pass, tool_calls=None):
    """Drive _run_test_phase with a scripted structured verdict + agent output.

    Returns the list of ``_update_workflow`` patches so the caller can assert on
    the route taken (test_retries / skip_retries / dev_round / status).
    """
    result = AgentTaskResult(
        session_id="sess",
        response_text=text,
        visible_response_text=text,
        success=True,
        tool_calls=(
            tool_calls
            if tool_calls is not None
            else [{"tool": {"name": "Bash", "input": {"command": "python -m pytest tests/ -q"}}}]
        ),
    )
    patches = []
    orch._update_workflow = lambda p: patches.append(p)
    orch._post_github_comment = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._find_or_create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._run_agent = MagicMock(return_value=result)
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._build_test_execution_context = MagicMock(return_value=None)
    orch._project_runtime_contract = MagicMock(return_value="")
    orch._artifact_visible_text = MagicMock(return_value=text)
    orch._artifact_text = MagicMock(return_value=text)
    orch._shadow_compare_evidence = MagicMock()
    orch._emit_structured_test_fallback = MagicMock()
    orch._validate_test_report_format = MagicMock(return_value=(True, ""))

    with (
        patch(_STRUCTURED, return_value=(verdict, [], "scripted")),
        patch(_PASSING_TOOL, return_value=tool_pass),
    ):
        orch._run_test_phase(wf, 1, orch._gh)
    return patches, orch


def _comment_text(orch) -> str:
    for call in orch._post_github_comment.call_args_list:
        body = call.args[2] if len(call.args) > 2 else call.kwargs.get("body", "")
        if "Test Results" in str(body):
            return str(body)
    return ""


# --- The defect the plan required a test for ---------------------------------


def test_structured_failed_is_not_rescued_by_agent_prose():
    # "1 failed, 243 passed" is the most common partial pytest failure and it
    # satisfies the #1830 prose regex. It must NOT let a structured FAILED
    # reach pr_review.
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="=== 1 failed, 243 passed in 30.12s ===\n测试有 1 个失败。",
        tool_pass=False,
    )
    assert any(
        "test_retries" in p for p in patches
    ), f"structured FAILED must take the inconclusive/test_retries route, got {patches}"
    assert not any(p.get("current_phase") == "pr_review" for p in patches)


def test_conclusive_rerun_pass_supersedes_structured_failed():
    # The load-bearing case for the two-arm collapse: a conclusive tool-result
    # pass (the heuristic's normalized-command latest-wins) DOES override a
    # structured FAILED, so a fail-then-rerun-pass session is not killed.
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="Tests: 40 passed",
        tool_pass=True,
    )
    assert not any(
        "test_retries" in p and p["test_retries"] > 0 for p in patches
    ), f"a conclusive rerun pass must not be forced onto the retry path, got {patches}"


def test_structured_failed_does_not_emit_the_parser_gap_counter():
    # _emit_structured_test_fallback counts *parser coverage gaps* so the
    # heuristic can be retired; a FAILED verdict is not a gap.
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    _, orch = _run(
        orch, wf, verdict=ExecutionVerdict.FAILED, text="1 failed, 2 passed", tool_pass=False
    )
    orch._emit_structured_test_fallback.assert_not_called()


@pytest.mark.parametrize("verdict", [ExecutionVerdict.NOT_RUN, ExecutionVerdict.INCONCLUSIVE])
def test_parser_gap_counter_still_emits_for_not_run_and_inconclusive(verdict):
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    _, orch = _run(orch, wf, verdict=verdict, text="1 failed, 2 passed", tool_pass=False)
    orch._emit_structured_test_fallback.assert_called_once()


def test_structured_failed_comment_does_not_claim_success_or_a_missing_result():
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    _, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="=== 1 failed, 243 passed in 30.12s ===",
        tool_pass=False,
    )
    body = _comment_text(orch)
    assert "All tests passed" not in body
    assert "no verifiable result" not in body
    assert "structured evidence reports a failing test command" in body


def test_structured_passed_still_proceeds():
    # Non-regression: PASSED remains authoritative.
    wf = _workflow()
    orch, _ = _orchestrator(wf)
    patches, orch = _run(
        orch, wf, verdict=ExecutionVerdict.PASSED, text="243 passed in 30.12s", tool_pass=False
    )
    assert not any(p.get("test_retries", 0) for p in patches)
