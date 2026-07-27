"""Phase B gate integration tests (#2046).

Exercises the orchestrator methods that wire the structured test verdict into
the gate: ``_compute_structured_test_verdict`` (parse + run verdict),
``_emit_structured_test_fallback`` (INCONCLUSIVE/NOT_RUN fallback signal),
and ``_shadow_compare_evidence`` with the new ``structured_verdict`` argument.

These are method-level tests — the repos are mocked so the structured path is
exercised without a full workflow run. The acceptance criterion is the #1967
invariant: a structured FAILED/PASSED is authoritative and never re-derived
from agent prose; INCONCLUSIVE/NOT_RUN defers to the heuristic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

_CMD_REPO = "app.repositories.command_evidence_repo.CommandExecutionEvidenceRepository"
_TEST_REPO = "app.repositories.test_evidence_repo.TestExecutionEvidenceRepository"
_RECORDER = (
    "app.modules.workspace.autonomous.command_evidence.recorder.get_command_evidence_recorder"
)


def _orch() -> AutonomousOrchestrator:
    """Build a bare orchestrator with the few attrs the gate methods touch."""
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-test"
    orch.repo = MagicMock()
    return orch


def _result(session_id: str = "sess-1") -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id, tracking_session_id=None)


def _ce(
    command_id: str, shell: str, exit_code: int | None, excerpt: str
) -> CommandExecutionEvidence:
    return CommandExecutionEvidence(
        command_id=command_id,
        session_id="sess-1",
        tool_name="Bash",
        shell_command=shell,
        exit_code=exit_code,
        output_excerpt=excerpt,
    )


def _patch_repos(command_evidences):
    """Patch the recorder (noop) + command repo (returns evidences) + test repo."""
    recorder_patch = patch(_RECORDER)
    cmd_patch = patch(_CMD_REPO)
    test_patch = patch(_TEST_REPO)
    recorder_mock = recorder_patch.start()
    cmd_mock = cmd_patch.start()
    test_mock = test_patch.start()
    recorder_mock.return_value.is_noop = True
    cmd_mock.return_value.query_by_session.return_value = command_evidences
    return recorder_patch, cmd_patch, test_patch, test_mock


# ── _compute_structured_test_verdict ──────────────────────────────────────────


def test_compute_verdict_passed_when_pytest_passes():
    orch = _orch()
    patches = _patch_repos([_ce("c1", "pytest", 0, "3 passed in 0.4s")])
    try:
        verdict, evidences, reason = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.PASSED
    assert len(evidences) == 1
    assert evidences[0].verdict == ExecutionVerdict.PASSED.value
    patches[3].return_value.upsert.assert_called_once()


def test_compute_verdict_failed_when_pytest_fails():
    orch = _orch()
    patches = _patch_repos([_ce("c1", "pytest tests/x.py", 1, "1 failed, 2 passed")])
    try:
        verdict, _, _ = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.FAILED


def test_compute_verdict_not_run_when_no_command_evidence():
    orch = _orch()
    patches = _patch_repos([])
    try:
        verdict, evidences, reason = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.NOT_RUN
    assert evidences == []
    assert "no command execution evidence" in reason


def test_compute_verdict_not_run_when_session_id_missing():
    orch = _orch()
    patches = _patch_repos([_ce("c1", "pytest", 0, "1 passed")])
    try:
        verdict, _, reason = orch._compute_structured_test_verdict(
            _result(session_id=""), "python", {}
        )
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.NOT_RUN
    assert "no session id" in reason


def test_compute_verdict_filters_out_non_test_commands():
    """An ``echo`` command must not be parsed as a test run (#2046)."""
    orch = _orch()
    patches = _patch_repos(
        [
            _ce("c1", "pytest", 0, "2 passed"),
            _ce("c2", "echo hello", 0, "hello"),
        ]
    )
    try:
        verdict, evidences, _ = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.PASSED
    assert len(evidences) == 1  # echo was filtered out
    assert evidences[0].command_id == "c1"


def test_compute_verdict_not_run_when_no_test_command_at_all():
    """Only non-test commands → no test evidence → NOT_RUN (heuristic fallback)."""
    orch = _orch()
    patches = _patch_repos([_ce("c1", "echo hello", 0, "hello")])
    try:
        verdict, _, reason = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.NOT_RUN
    assert "no test command evidence" in reason


def test_compute_verdict_inconclusive_when_command_unparseable():
    """A test command whose output could not be parsed (no exit code, no
    summary) → INCONCLUSIVE → gate falls back to the heuristic."""
    orch = _orch()
    patches = _patch_repos([_ce("c1", "pytest", None, "starting...")])
    try:
        verdict, _, _ = orch._compute_structured_test_verdict(_result(), "python", {})
    finally:
        for p in patches[:3]:
            p.stop()
    assert verdict == ExecutionVerdict.INCONCLUSIVE


def test_compute_verdict_exception_returns_not_run_best_effort():
    """Any failure in the structured path must NOT raise into the gate."""
    orch = _orch()
    with patch(_CMD_REPO) as cmd_mock:
        cmd_mock.return_value.query_by_session.side_effect = RuntimeError("db down")
        with patch(_RECORDER) as rec:
            rec.return_value.is_noop = True
            verdict, evidences, reason = orch._compute_structured_test_verdict(
                _result(), "python", {}
            )
    assert verdict == ExecutionVerdict.NOT_RUN
    assert "compute failed" in reason


# ── _emit_structured_test_fallback ────────────────────────────────────────────


def test_emit_structured_fallback_records_workflow_event():
    orch = _orch()
    orch._emit_structured_test_fallback(
        ExecutionVerdict.INCONCLUSIVE, "all commands generic/LOW", "ms-1"
    )
    orch.repo.create_event.assert_called_once()
    event = orch.repo.create_event.call_args[0][0]
    assert event["event_type"] == "structured_test_fallback"
    assert event["milestone_id"] == "ms-1"
    assert '"structured_verdict": "inconclusive"' in event["event_data"]


def test_emit_structured_fallback_never_raises():
    """Best-effort: a repo failure must not propagate into the gate."""
    orch = _orch()
    orch.repo.create_event.side_effect = RuntimeError("db down")
    orch._emit_structured_test_fallback(ExecutionVerdict.NOT_RUN, "reason", "ms-1")  # no raise


# ── _shadow_compare_evidence (Phase B run-level divergence) ───────────────────


def test_shadow_compare_records_run_level_divergence_structured_vs_heuristic():
    """Heuristic said pass but structured said FAILED → divergence recorded."""
    orch = _orch()
    with patch(_RECORDER) as rec:
        rec.return_value.is_noop = True  # skip DB evidence fetch
        orch._shadow_compare_evidence(
            test_result=_result(),
            milestone_id="ms-1",
            heuristic_passed=True,
            structured_verdict=ExecutionVerdict.FAILED,
        )
    orch.repo.create_event.assert_called_once()
    event = orch.repo.create_event.call_args[0][0]
    assert event["event_type"] == "evidence_shadow_divergence"


def test_shadow_compare_no_event_when_structured_agrees_with_heuristic():
    """Heuristic did not pass and structured FAILED → both agree, no divergence."""
    orch = _orch()
    with patch(_RECORDER) as rec:
        rec.return_value.is_noop = True
        orch._shadow_compare_evidence(
            test_result=_result(),
            milestone_id="ms-1",
            heuristic_passed=False,
            structured_verdict=ExecutionVerdict.FAILED,
        )
    orch.repo.create_event.assert_not_called()
