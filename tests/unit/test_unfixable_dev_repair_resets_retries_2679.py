"""Situation B ([UNFIXABLE]) must reset the retry counters on its repair bump (#2679).

``_run_test_phase``'s [UNFIXABLE] branch starts a dev-repair round by bumping
``dev_round`` — but it used to leave ``test_retries``/``skip_retries`` at
their stale values. ``phases/development.py`` skips the dev agent whenever
``test_retries > 0 or skip_retries > 0``, so a repair round entered with a
stale counter never invoked the dev agent: the "repair" was just another test
re-run, and both ``dev_retries_on_test_fail`` slots could evaporate without a
single repair attempt. Same bug class as #2590, different branch (PR #2676
fixed the structured-FAILED twin).

These tests drive ``_run_test_phase`` end-to-end and pin the routing,
mirroring the proven harness in tests/unit/test_test_evidence_requirer.py
(which reaches the same [UNFIXABLE] branch).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2679)]

from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict
from app.modules.workspace.autonomous.models import AgentTaskResult

_STRUCTURED = (
    "app.modules.workspace.autonomous.orchestrator."
    "AutonomousOrchestrator._compute_structured_test_verdict"
)
_PASSING_TOOL = "app.modules.workspace.autonomous.orchestrator._has_passing_test_tool_result"


def _workflow(**overrides):
    base = {
        "workflow_id": "wf-2679",
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
        "github_issue_number": 2679,
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
        repo.create_milestone.return_value = {
            "milestone_id": "ms-2679",
            "workflow_id": "wf-2679",
        }
        repo.update_workflow.return_value = wf
        repo.update_milestone.return_value = {}
        repo.create_event.return_value = {"id": 1}
        repo_cls.return_value = repo
        orch = AutonomousOrchestrator("wf-2679")
        orch.repo = repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.has_uncommitted_changes.return_value = False
        return orch


def _run(orch, wf, *, text):
    """Drive ``_run_test_phase`` to Situation B: the agent session succeeded,
    the structured verdict is PASSED (so the run is neither inconclusive nor
    skipped), and the report declares ``[UNFIXABLE]``. Returns the
    ``_update_workflow`` patches so the caller can assert the route taken."""
    result = AgentTaskResult(
        session_id="sess",
        response_text=text,
        visible_response_text=text,
        success=True,
        tool_calls=[{"tool": {"name": "Bash", "input": {"command": "python -m pytest tests/ -q"}}}],
    )
    patches: list[dict] = []
    orch._update_workflow = lambda p: patches.append(p)
    orch._post_github_comment = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-2679"})
    orch._find_or_create_milestone = MagicMock(return_value={"milestone_id": "ms-2679"})
    orch._run_agent = MagicMock(return_value=result)
    orch._runtime_environment_gate = MagicMock(return_value="")
    # No changed files: the #2391 requirer then requires no evidence domain,
    # so shadow/enforce alike leave the [UNFIXABLE] routing untouched.
    orch._build_test_execution_context = MagicMock(return_value=("", []))
    orch._project_runtime_contract = MagicMock(return_value="")
    orch._artifact_visible_text = MagicMock(return_value=text)
    orch._artifact_text = MagicMock(return_value=text)
    orch._shadow_compare_evidence = MagicMock()
    orch._emit_structured_test_fallback = MagicMock()
    orch._validate_test_report_format = MagicMock(return_value=(True, ""))
    with (
        patch(_STRUCTURED, return_value=(ExecutionVerdict.PASSED, [], "scripted")),
        patch(_PASSING_TOOL, return_value=False),
    ):
        orch._run_test_phase(wf, 1, orch._gh)
    return patches


# --- The [UNFIXABLE] repair round must actually reach the dev agent ---------


def test_unfixable_repair_round_resets_test_and_skip_retries():
    """Issue #2679's scenario: inconclusive/structured-FAILED retries climb
    ``test_retries`` (and possibly ``skip_retries``) first, then the agent
    declares [UNFIXABLE]. The dev-repair bump must clear both counters —
    otherwise phases/development.py keeps skipping the dev agent and the
    repair round degrades into a test-only re-run."""
    wf = _workflow(test_retries=2, skip_retries=1, dev_retries_on_test_fail=0)
    orch = _orchestrator(wf)

    patches = _run(orch, wf, text="243 passed, but the flaky harness is [UNFIXABLE]")

    dev_bumps = [p for p in patches if "dev_round" in p]
    assert dev_bumps, f"expected a dev-repair round, got {patches}"
    bump = dev_bumps[0]
    assert bump.get("dev_round") == 2, bump
    assert bump.get("dev_retries_on_test_fail") == 1, bump
    assert bump.get("test_retries") == 0, (
        "stale test_retries keeps the dev agent skipped on the repair " f"round: {bump}"
    )
    assert bump.get("skip_retries") == 0, (
        "stale skip_retries keeps the dev agent skipped on the repair " f"round: {bump}"
    )
    # The repair round is not terminal failure.
    assert not any(p.get("status") == "failed" for p in patches), patches


def test_unfixable_with_stale_test_retries_only_also_resets():
    """Boundary: only ``test_retries`` is stale (``skip_retries == 0``) — the
    reset must still be present in the bump so the dev agent runs."""
    wf = _workflow(test_retries=1, skip_retries=0, dev_retries_on_test_fail=0)
    orch = _orchestrator(wf)

    patches = _run(orch, wf, text="Tests pass locally; CI flake is [UNFIXABLE]")

    dev_bumps = [p for p in patches if "dev_round" in p]
    assert dev_bumps, f"expected a dev-repair round, got {patches}"
    bump = dev_bumps[0]
    assert bump.get("test_retries") == 0, bump
    assert bump.get("skip_retries") == 0, bump


def test_unfixable_dev_retries_exhausted_still_terminal():
    """The reset must not change the exhaustion boundary: once
    ``dev_retries_on_test_fail`` is spent, [UNFIXABLE] stays terminal failed
    regardless of the retry counters."""
    wf = _workflow(test_retries=1, dev_retries_on_test_fail=2)
    orch = _orchestrator(wf)

    patches = _run(orch, wf, text="243 passed, but the flaky harness is [UNFIXABLE]")

    assert any(p.get("status") == "failed" for p in patches), patches
    assert not any("dev_round" in p for p in patches), patches
