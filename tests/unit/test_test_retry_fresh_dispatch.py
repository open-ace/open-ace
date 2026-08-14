"""#2663: a test-phase retry must re-execute tests, not cite prior rounds.

``_run_test_phase`` dispatches with ``session_line="test"``, resuming the SAME
CLI session across retries. The dev agent already ran tests during
development, so on a retry it answers by citing prior-round results instead of
executing anything new — the evidence gate (correctly) sees no fresh output
and the workflow burns every retry on ``inconclusive`` (#2590's workflow, two
occurrences, the second after all empty-result fixes were deployed).

Fix under test: on a retry (``test_retries > 0``) the dispatch switches to
``session_line="fresh"`` (no prior round in context to cite) and the prompt
gains an explicit retry-instruction block demanding fresh execution with raw
output. The first run (``test_retries == 0``) keeps resuming the test line —
the dev session's context is valuable there.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2663)]

_STRUCTURED = (
    "app.modules.workspace.autonomous.orchestrator."
    "AutonomousOrchestrator._compute_structured_test_verdict"
)
_PASSING_TOOL = "app.modules.workspace.autonomous.orchestrator._has_passing_test_tool_result"
_TEST_REPO = "app.repositories.test_evidence_repo.TestExecutionEvidenceRepository"

RETRY_BLOCK_MARKER = "上一轮验证未通过证据门"


def _workflow(**overrides):
    base = {
        "workflow_id": "wf-2663",
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
        "github_issue_number": 2663,
        "test_retries": 0,
        "skip_retries": 0,
        "dev_retries_on_test_fail": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _dispatch(test_retries: int) -> dict:
    """Drive ``_run_test_phase`` once; return the ``_run_agent`` kwargs."""
    from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict
    from app.modules.workspace.autonomous.models import AgentTaskResult
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    wf = _workflow(test_retries=test_retries)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as repo_cls,
    ):
        repo = MagicMock()
        repo.get_workflow.return_value = wf
        repo.list_milestones.return_value = []
        repo.create_milestone.return_value = {"milestone_id": "ms-cur", "workflow_id": "wf-2663"}
        repo.update_workflow.return_value = wf
        repo.update_milestone.return_value = {}
        repo.create_event.return_value = {"id": 1}
        repo_cls.return_value = repo
        orch = AutonomousOrchestrator("wf-2663")
    orch.repo = repo
    orch.emitter = MagicMock()
    orch._gh = MagicMock()
    orch._gh.has_uncommitted_changes.return_value = False

    text = "TEST_STATUS: PASSED\n2 passed"
    result = AgentTaskResult(
        session_id="sess",
        response_text=text,
        visible_response_text=text,
        success=True,
        tool_calls=[{"tool": {"name": "Bash", "input": {"command": "python -m pytest tests/ -q"}}}],
    )
    orch._update_workflow = lambda p: None
    orch._post_github_comment = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-cur"})
    orch._find_or_create_milestone = MagicMock(return_value={"milestone_id": "ms-cur"})
    orch._run_agent = MagicMock(return_value=result)
    orch._accumulate_tokens = MagicMock()
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._build_test_execution_context = MagicMock(return_value=("", []))
    orch._project_runtime_contract = MagicMock(return_value="")
    orch._artifact_visible_text = MagicMock(return_value=text)
    orch._artifact_text = MagicMock(return_value=text)
    orch._shadow_compare_evidence = MagicMock()
    orch._emit_structured_test_fallback = MagicMock()
    orch._apply_test_evidence_requirer = MagicMock(return_value=("shadow", []))
    orch._validate_test_report_format = MagicMock(return_value=(True, ""))

    with (
        patch(_STRUCTURED, return_value=(ExecutionVerdict.PASSED, [], "scripted")),
        patch(_PASSING_TOOL, return_value=True),
        patch(_TEST_REPO) as test_repo_cls,
    ):
        test_repo_cls.return_value.query_by_milestone.return_value = []
        orch._run_test_phase(wf, 1, orch._gh)
    return orch._run_agent.call_args.kwargs


def test_first_run_resumes_test_line_without_retry_block():
    kwargs = _dispatch(test_retries=0)
    assert kwargs["session_line"] == "test"
    assert RETRY_BLOCK_MARKER not in kwargs["prompt"]


def test_retry_dispatches_fresh_session_with_retry_instructions():
    kwargs = _dispatch(test_retries=1)
    assert kwargs["session_line"] == "fresh"
    prompt = kwargs["prompt"]
    assert RETRY_BLOCK_MARKER in prompt
    # The instruction must demand re-execution and forbid citing prior rounds.
    assert "重新执行" in prompt
    assert "不得引用之前回合" in prompt
