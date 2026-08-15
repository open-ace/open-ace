"""Carry forward the prior test milestone's structured verdict (#2590, Option A).

On a test-phase retry, the agent session is reused (same ``session_id``). The
#2390 milestone-scoped filter correctly yields ``NOT_RUN`` for the current
milestone — the agent saw the full prior context and *summarized* the old
results instead of re-running the tests, so no command evidence was stamped
for this round. But the gate then falls back to the legacy heuristic, which
reads the *entire* session text (including the prior round's "N passed")
and misclassifies the run as ``inconclusive`` instead of carrying forward the
prior round's real FAILED verdict.

The fix: when the current milestone's structured verdict is ``NOT_RUN`` and
this is a retry (``test_retries > 0``), recompute the prior test milestone's
verdict from its persisted ``test_execution_evidence`` rows and carry it
forward — FAILED stays FAILED (keeps retrying), PASSED proceeds. No prior
milestone or prior NOT_RUN falls through to the unchanged heuristic.

These tests drive ``_run_test_phase`` (gate behaviour, not just verdict
behaviour) so the assertion pins the routing, not merely a return value.
Mirrors tests/issues/2376/test_gate_flags.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2590)]

from app.modules.workspace.autonomous.command_evidence.test_evidence import TestExecutionEvidence
from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict
from app.modules.workspace.autonomous.models import AgentTaskResult

_STRUCTURED = (
    "app.modules.workspace.autonomous.orchestrator."
    "AutonomousOrchestrator._compute_structured_test_verdict"
)
_PASSING_TOOL = "app.modules.workspace.autonomous.orchestrator._has_passing_test_tool_result"
_COMPUTE_RUN = "app.modules.workspace.autonomous.command_evidence.test_verdict.compute_run_verdict"
_TEST_REPO = "app.repositories.test_evidence_repo.TestExecutionEvidenceRepository"


def _workflow(**overrides):
    base = {
        "workflow_id": "wf-2590",
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
        "github_issue_number": 2590,
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
        repo.create_milestone.return_value = {"milestone_id": "ms-cur", "workflow_id": "wf-2590"}
        repo.update_workflow.return_value = wf
        repo.update_milestone.return_value = {}
        repo.create_event.return_value = {"id": 1}
        repo_cls.return_value = repo
        orch = AutonomousOrchestrator("wf-2590")
        orch.repo = repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.has_uncommitted_changes.return_value = False
        return orch, repo


def _run(
    orch,
    wf,
    *,
    verdict,
    text,
    tool_pass,
    prior_evidences=None,
    prior_milestones=None,
    evidences=None,
):
    """Drive _run_test_phase with a scripted structured verdict + agent output.

    ``prior_evidences`` sets what the prior milestone's test-evidence repo
    returns (used by carry-forward). ``prior_milestones`` sets what
    ``list_milestones`` returns so the carry-forward can find the prior test
    milestone. ``evidences`` sets the CURRENT milestone's structured evidence
    rows (the second element of ``_compute_structured_test_verdict``).
    """
    result = AgentTaskResult(
        session_id="sess",
        response_text=text,
        visible_response_text=text,
        success=True,
        tool_calls=[{"tool": {"name": "Bash", "input": {"command": "python -m pytest tests/ -q"}}}],
    )
    patches = []
    orch._update_workflow = lambda p: patches.append(p)
    orch._post_github_comment = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-cur"})
    orch._find_or_create_milestone = MagicMock(return_value={"milestone_id": "ms-cur"})
    orch._run_agent = MagicMock(return_value=result)
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._build_test_execution_context = MagicMock(return_value=("", []))
    orch._project_runtime_contract = MagicMock(return_value="")
    orch._artifact_visible_text = MagicMock(return_value=text)
    orch._artifact_text = MagicMock(return_value=text)
    orch._shadow_compare_evidence = MagicMock()
    orch._emit_structured_test_fallback = MagicMock()
    orch._validate_test_report_format = MagicMock(return_value=(True, ""))

    orch.repo.list_milestones.return_value = prior_milestones or []

    with (
        patch(_STRUCTURED, return_value=(verdict, evidences or [], "scripted")),
        patch(_PASSING_TOOL, return_value=tool_pass),
        patch(_TEST_REPO) as _test_repo_cls,
    ):
        # Default: no prior evidence; caller overrides.
        _test_repo_cls.return_value.query_by_milestone.return_value = prior_evidences or []
        orch._run_test_phase(wf, 1, orch._gh)
    return patches, orch


def _comment_text(orch) -> str:
    for call in orch._post_github_comment.call_args_list:
        body = call.args[2] if len(call.args) > 2 else call.kwargs.get("body", "")
        if "Test Results" in str(body):
            return str(body)
    return ""


# --- Carry-forward: prior FAILED keeps the run failing, not inconclusive ----


def test_prior_failed_is_carried_forward_not_inconclusive():
    """A retry whose current milestone has NO evidence but whose prior milestone
    structurally FAILED must carry FAILED forward — not fall to the heuristic
    and report inconclusive on stale session text."""
    wf = _workflow(test_retries=1)
    orch, _ = _orchestrator(wf)

    # Prior milestone ms-prev was FAILED. The agent's stale session text has
    # "N passed" from the prior round (which the heuristic would read), but
    # the prior structured verdict is the source of truth.
    prior_ev = TestExecutionEvidence(
        command_id="c-prev",
        verdict=ExecutionVerdict.FAILED.value,
        framework="python",
        parser_confidence="high",
        passed=1,
        failed=1,
    )
    prior_ms = [
        {
            "milestone_id": "ms-prev",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 1,
            "id": 10,
        },
        {
            "milestone_id": "ms-cur",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 1,
            "id": 20,
        },
    ]

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.NOT_RUN,
        text="=== 243 passed in 30.12s ===",
        tool_pass=False,
        prior_evidences=[prior_ev],
        prior_milestones=prior_ms,
    )
    # FAILED carry-forward takes the inconclusive/test_retries route (stays
    # failing), NOT pr_review.
    assert any(
        "test_retries" in p for p in patches
    ), f"prior FAILED must carry forward (retry), got {patches}"
    assert not any(p.get("current_phase") == "pr_review" for p in patches), patches
    # The status line must report the failed verdict, not "no output captured".
    comment = _comment_text(orch)
    assert "structured evidence reports a failing test command" in comment, comment


def test_prior_passed_is_carried_forward_proceeds():
    """A retry whose current milestone has NO evidence but whose prior milestone
    structurally PASSED must carry PASSED forward — proceed to pr_review."""
    wf = _workflow(test_retries=1)
    orch, _ = _orchestrator(wf)

    prior_ev = TestExecutionEvidence(
        command_id="c-prev",
        verdict=ExecutionVerdict.PASSED.value,
        framework="python",
        parser_confidence="high",
        passed=243,
        failed=0,
    )
    prior_ms = [
        {
            "milestone_id": "ms-prev",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 1,
            "id": 10,
        },
        {
            "milestone_id": "ms-cur",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 1,
            "id": 20,
        },
    ]

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.NOT_RUN,
        text="=== 243 passed in 30.12s ===",
        tool_pass=False,
        prior_evidences=[prior_ev],
        prior_milestones=prior_ms,
    )
    assert any(
        p.get("current_phase") == "pr_review" for p in patches
    ), f"prior PASSED must proceed to pr_review, got {patches}"


def test_no_prior_milestone_keeps_existing_behavior():
    """First attempt (test_retries=0) with NOT_RUN: no carry-forward, the gate
    behaves as before (heuristic / inconclusive). No regression."""
    wf = _workflow(test_retries=0)
    orch, _ = _orchestrator(wf)

    # No prior milestones at all. Text has a pytest marker ("AssertionError")
    # but NOT the "N passed" pattern, so the heuristic cannot confirm it
    # passed → inconclusive. The point is this is the UNCHANGED existing path.
    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.NOT_RUN,
        text="=== AssertionError ===",
        tool_pass=False,
        prior_evidences=[],
        prior_milestones=[],
    )
    # Must NOT proceed to pr_review (no evidence + no "N passed" text → the
    # heuristic cannot confirm). The exact route is whatever the existing logic
    # decides — the assertion is "no carry-forward changed it".
    assert not any(p.get("current_phase") == "pr_review" for p in patches), patches


def test_carry_forward_only_on_retry():
    """test_retries=0 must never carry forward — a first attempt has no prior
    milestone to read, even if stale milestones exist in the DB."""
    wf = _workflow(test_retries=0)
    orch, _ = _orchestrator(wf)

    prior_ev = TestExecutionEvidence(
        command_id="c-prev",
        verdict=ExecutionVerdict.FAILED.value,
        framework="python",
        parser_confidence="high",
        passed=1,
        failed=1,
    )
    prior_ms = [
        {
            "milestone_id": "ms-prev",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 0,
            "id": 5,
        },
        {
            "milestone_id": "ms-cur",
            "workflow_id": "wf-2590",
            "phase": "development",
            "milestone_type": "tests_run",
            "dev_round": 1,
            "id": 6,
        },
    ]

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.NOT_RUN,
        text="=== 1 failed ===",
        tool_pass=False,
        prior_evidences=[prior_ev],
        prior_milestones=prior_ms,
    )
    # test_retries=0 → no carry-forward → must NOT report the prior FAILED
    # verdict in the comment (the existing heuristic path applies instead).
    comment = _comment_text(orch)
    assert (
        "structured evidence reports a failing test command" not in comment
    ), "carry-forward must not fire on test_retries=0"


# --- #2590 Option A: exhausted structured FAILED routes into a dev-repair round


def _failed_evidence():
    return TestExecutionEvidence(
        command_id="c-fail",
        verdict=ExecutionVerdict.FAILED.value,
        framework="python",
        parser_confidence="high",
        passed=243,
        failed=3,
    )


def test_structured_failed_at_retry_exhaustion_enters_dev_repair_round():
    """(a) test retries exhausted + decisive structured FAILED → dev-repair
    round (same counter/cap as Situation B), NOT terminal failed; the failing
    output rides in ``user_feedback`` so the dev prompt starts from it."""
    wf = _workflow(test_retries=2, dev_retries_on_test_fail=0)
    orch, _ = _orchestrator(wf)

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text=(
            "=== 3 failed, 243 passed in 30.12s ===\n"
            "FAILED tests/unit/test_widget.py::test_click"
        ),
        tool_pass=False,
        evidences=[_failed_evidence()],
    )
    dev_bumps = [p for p in patches if "dev_round" in p]
    assert dev_bumps, f"expected a dev-repair round, got {patches}"
    bump = dev_bumps[0]
    assert bump.get("dev_round") == 2
    assert bump.get("dev_retries_on_test_fail") == 1
    # The repaired code gets a fresh test-phase budget: with test_retries left
    # exhausted, the new round's first FAILED would skip test retries entirely
    # and burn a dev round on a run the test agent could have settled itself.
    assert bump.get("test_retries") == 0
    feedback = bump.get("user_feedback") or ""
    # The structured failure summary (counts) and the test report excerpt
    # (raw failing output) must both reach the dev round.
    assert "failed=3" in feedback, feedback
    assert "tests/unit/test_widget.py::test_click" in feedback, feedback
    # Workflow stays developing — no terminal failure.
    assert not any(p.get("status") == "failed" for p in patches), patches


def test_dev_repair_round_resets_skip_retries_too():
    """The dev-repair bump must clear ``skip_retries`` alongside
    ``test_retries``: phases/development.py skips the dev agent when
    ``test_retries > 0 OR skip_retries > 0``, so a stale ``skip_retries=1``
    (e.g. a skipped-tests retry earlier in the same round) would make BOTH
    repair retries test-only re-runs — the dev agent is never invoked and the
    failures can never be fixed."""
    wf = _workflow(test_retries=2, skip_retries=1, dev_retries_on_test_fail=0)
    orch, _ = _orchestrator(wf)

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="=== 3 failed, 243 passed in 30.12s ===",
        tool_pass=False,
        evidences=[_failed_evidence()],
    )
    dev_bumps = [p for p in patches if "dev_round" in p]
    assert dev_bumps, f"expected a dev-repair round, got {patches}"
    bump = dev_bumps[0]
    assert bump.get("skip_retries") == 0, (
        "stale skip_retries=1 keeps the dev agent skipped on the repair " f"round: {bump}"
    )
    assert bump.get("test_retries") == 0, bump


def test_structured_failed_before_retry_exhaustion_retries_tests_first():
    """Boundary: a structured FAILED with retries remaining (test_retries=1 <
    MAX_TEST_RETRIES=2) takes the plain test-retry path — bump test_retries,
    no dev_round, no user_feedback. The dev-repair round is reserved for
    exhaustion; jumping early would waste dev rounds on flakes the test agent
    can settle itself."""
    wf = _workflow(test_retries=1, dev_retries_on_test_fail=0)
    orch, _ = _orchestrator(wf)

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="=== 3 failed, 243 passed in 30.12s ===",
        tool_pass=False,
        evidences=[_failed_evidence()],
    )
    retry_bumps = [p for p in patches if "test_retries" in p]
    assert retry_bumps, f"expected a test retry, got {patches}"
    assert retry_bumps[0].get("test_retries") == 2, retry_bumps[0]
    assert not any("dev_round" in p for p in patches), patches
    assert not any("user_feedback" in p for p in patches), patches
    assert not any(p.get("status") == "failed" for p in patches), patches


def test_repair_feedback_excerpt_preserves_tail():
    """The report excerpt must be tail-preserving: pytest-style output puts the
    actionable summary (short summary of failures) at the END — a head-only
    cut at 6000 chars discards exactly what the dev round needs. The excerpt
    keeps head+tail with a truncation marker between."""
    from app.modules.workspace.autonomous.orchestrator import _head_tail_excerpt

    long_report = "A" * 4000 + "B" * 3000 + "SHORT SUMMARY: test_click FAILED\n"
    excerpt = _head_tail_excerpt(long_report, 6000)
    assert "SHORT SUMMARY: test_click FAILED" in excerpt, "tail content must survive truncation"
    assert excerpt.startswith("A" * 10), "head content must survive truncation"
    assert "…[truncated]…" in excerpt
    # Short reports pass through untouched.
    assert _head_tail_excerpt("short", 6000) == "short"


def test_structured_failed_dev_retries_exhausted_is_terminal():
    """(b) dev-repair retries ALSO exhausted with structured FAILED → terminal
    failed, and the message mentions both counters."""
    wf = _workflow(test_retries=2, dev_retries_on_test_fail=2)
    orch, _ = _orchestrator(wf)

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.FAILED,
        text="=== 3 failed, 243 passed in 30.12s ===",
        tool_pass=False,
        evidences=[_failed_evidence()],
    )
    failures = [p for p in patches if p.get("status") == "failed"]
    assert failures, f"expected terminal failure, got {patches}"
    message = failures[0].get("error_message", "")
    assert "test retries" in message, message
    assert "dev" in message, message
    assert not any("dev_round" in p for p in patches), patches


def test_inconclusive_at_retry_exhaustion_keeps_terminal_path():
    """(c) inconclusive exhaustion is unchanged: terminal failed, no dev round,
    no user_feedback written — there is no actionable failure to hand over."""
    wf = _workflow(test_retries=2)
    orch, _ = _orchestrator(wf)

    patches, orch = _run(
        orch,
        wf,
        verdict=ExecutionVerdict.NOT_RUN,
        text="=== AssertionError ===",
        tool_pass=False,
    )
    failures = [p for p in patches if p.get("status") == "failed"]
    assert failures, f"expected terminal failure, got {patches}"
    assert failures[0].get("error_message", "").startswith("Test execution is inconclusive")
    assert not any("dev_round" in p for p in patches), patches
    assert not any("user_feedback" in p for p in patches), patches
