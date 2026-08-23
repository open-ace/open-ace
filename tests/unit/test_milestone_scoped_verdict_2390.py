"""The structured verdict is scoped to the current test milestone (#2390).

``_compute_structured_test_verdict`` read evidence via ``query_by_session``, which
is unbounded — ``session_id`` is stable across dev rounds, so a prior round's
evidence accumulated into the current round's verdict. That forces the
authoritative structured layer to defer (non-pytest → INCONCLUSIVE) or even
false-FAIL (pytest broad→targeted) instead of confirming a clean current round.

The fix scopes the session evidence to the current test milestone
(``test_ms["milestone_id"]``), but only when milestone stamping is present in the
session — an unstamped legacy session keeps session scope rather than silently
resolving NOT_RUN. Method-level, repos mocked (mirrors tests/issues/2046).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

pytestmark = [pytest.mark.regression, pytest.mark.issue(2390)]

_CMD_REPO = "app.repositories.command_evidence_repo.CommandExecutionEvidenceRepository"
_TEST_REPO = "app.repositories.test_evidence_repo.TestExecutionEvidenceRepository"
_RECORDER = (
    "app.modules.workspace.autonomous.command_evidence.recorder.get_command_evidence_recorder"
)


def _orch() -> AutonomousOrchestrator:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-test"
    orch.repo = MagicMock()
    return orch


def _ce(command_id: str, milestone_id: str, shell: str, exit_code: int, excerpt: str):
    return CommandExecutionEvidence(
        command_id=command_id,
        session_id="sess-1",
        milestone_id=milestone_id,
        tool_name="Bash",
        shell_command=shell,
        exit_code=exit_code,
        output_excerpt=excerpt,
    )


def _compute(command_evidences, framework, test_ms):
    orch = _orch()
    recorder_patch = patch(_RECORDER)
    cmd_patch = patch(_CMD_REPO)
    test_patch = patch(_TEST_REPO)
    recorder_mock = recorder_patch.start()
    cmd_mock = cmd_patch.start()
    test_patch.start()
    recorder_mock.return_value.is_noop = True
    cmd_mock.return_value.query_by_session.return_value = command_evidences
    try:
        return orch._compute_structured_test_verdict(
            SimpleNamespace(session_id="sess-1", tracking_session_id=None), framework, test_ms
        )
    finally:
        recorder_patch.stop()
        cmd_patch.stop()
        test_patch.stop()


def test_prior_round_failure_does_not_pollute_current_verdict():
    # Round 1 (ms-old) failed a targeted npm run; round 2 (ms-cur) passed the
    # suite. The session holds both.
    rows = [
        _ce("c1", "ms-old", "npm test -- foo", 1, "1 failing"),
        _ce("c2", "ms-cur", "npm test", 0, "ok, all passing"),
    ]
    # Unscoped (no current milestone): the stale round-1 failure is undecidably
    # (non-pytest) not-covered → the structured layer defers.
    v_unscoped, _, _ = _compute(rows, "javascript", {})
    assert v_unscoped == ExecutionVerdict.INCONCLUSIVE
    # Scoped to the current milestone: only round-2's pass is considered.
    v_scoped, ev, _ = _compute(rows, "javascript", {"milestone_id": "ms-cur"})
    assert v_scoped == ExecutionVerdict.PASSED
    assert len(ev) == 1


def test_pytest_broad_fail_then_targeted_pass_across_rounds_is_not_false_failed():
    # The pytest variant: a prior round's broad FAIL + this round's targeted PASS
    # is *decidable* non-coverage → unscoped FAILED (a false failure). Scoping to
    # the current round drops the stale broad fail. Scope-parseable commands only
    # (no --cov/pipe/wrapper), else _pytest_test_scope returns None.
    rows = [
        _ce("c1", "ms-old", "pytest tests/", 1, "1 failed, 9 passed"),
        _ce("c2", "ms-cur", "pytest tests/test_auth.py::test_login", 0, "1 passed"),
    ]
    v_unscoped, _, _ = _compute(rows, "python", {})
    assert v_unscoped == ExecutionVerdict.FAILED
    v_scoped, ev, _ = _compute(rows, "python", {"milestone_id": "ms-cur"})
    assert v_scoped == ExecutionVerdict.PASSED
    assert len(ev) == 1


def test_unstamped_session_falls_back_to_session_scope():
    # No row carries a milestone_id (legacy). Even with a current milestone, do
    # NOT narrow — narrowing would drop everything and resolve NOT_RUN. Keep the
    # session scope (current behavior).
    rows = [_ce("c1", "", "pytest tests/", 0, "3 passed")]
    v, ev, _ = _compute(rows, "python", {"milestone_id": "ms-cur"})
    assert v == ExecutionVerdict.PASSED
    assert len(ev) == 1


def test_partial_stamping_current_milestone_empty_is_fail_closed():
    # Stamping is present in the session (c-old has a milestone) but the current
    # milestone has no evidence → NOT_RUN, never a wrong PASSED from stale rows.
    rows = [_ce("c-old", "ms-old", "pytest tests/", 0, "3 passed")]
    v, ev, reason = _compute(rows, "python", {"milestone_id": "ms-cur"})
    assert v == ExecutionVerdict.NOT_RUN
    assert ev == []
    assert "current test milestone" in reason
