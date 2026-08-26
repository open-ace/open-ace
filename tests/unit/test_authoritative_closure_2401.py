"""End-to-end: a fail-open tool name cannot reach the authoritative PASSED (#2401 a).

The recognizer tests prove ``_has_test_tool_call`` rejects a bare ``test`` name;
this proves the *wiring* — that the evidence filter in
``_compute_structured_test_verdict`` uses the recognizer, so a
``tool_name="test"`` row with an empty (or non-test) command and exit 0 is
excluded and the run resolves NOT_RUN rather than the authoritative PASSED it
reached before. Method-level, repos mocked (mirrors tests/unit (ex tests/issues/2046)).
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

pytestmark = [pytest.mark.regression, pytest.mark.issue(2401)]

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


def _result(session_id: str = "sess-1") -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id, tracking_session_id=None)


def _ce(
    tool_name: str, shell: str, exit_code: int | None, excerpt: str
) -> CommandExecutionEvidence:
    return CommandExecutionEvidence(
        command_id="c1",
        session_id="sess-1",
        milestone_id="ms-1",
        tool_name=tool_name,
        shell_command=shell,
        exit_code=exit_code,
        output_excerpt=excerpt,
    )


def _compute(command_evidences, framework="python", test_ms=None):
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
        return orch._compute_structured_test_verdict(_result(), framework, test_ms or {})
    finally:
        recorder_patch.stop()
        cmd_patch.stop()
        test_patch.stop()


def test_empty_named_test_evidence_cannot_reach_passed():
    # Pre-#2401 this reached the authoritative PASSED (name branch → generic
    # parser → exit 0 + output → MEDIUM PASSED). Now the row is not recognized as
    # a test invocation, so there is no test command evidence → NOT_RUN.
    verdict, evidences, reason = _compute([_ce("test", "", 0, "done")])
    assert verdict != ExecutionVerdict.PASSED
    assert verdict == ExecutionVerdict.NOT_RUN
    assert evidences == []
    assert "no test command evidence" in reason


def test_non_test_command_under_test_name_cannot_reach_passed():
    verdict, evidences, _ = _compute([_ce("test", "helm install mocha ./chart", 0, "deployed")])
    assert verdict != ExecutionVerdict.PASSED
    assert evidences == []


def test_non_test_command_under_test_name_rejected_in_mixed_repo():
    # In a mixed repo the pattern union includes ``mocha``; the artifact-op veto
    # (``helm install mocha``) keeps it rejected.
    verdict, evidences, _ = _compute(
        [_ce("test", "helm install mocha ./chart", 0, "deployed")], framework="mixed"
    )
    assert verdict != ExecutionVerdict.PASSED
    assert evidences == []


def test_real_pytest_under_test_name_still_reaches_passed():
    # The fix must not break a dedicated test tool that actually ran pytest.
    verdict, evidences, _ = _compute([_ce("test", "pytest tests/", 0, "5 passed in 0.3s")])
    assert verdict == ExecutionVerdict.PASSED
    assert len(evidences) == 1
