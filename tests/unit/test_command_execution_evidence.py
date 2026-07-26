"""Tests for the CommandExecutionEvidence contract (#2046 Phase A).

Covers the pure logic (terminal-reason derivation, structured verdict, output
digest) and the shadow comparison, without a database. The repo/recorder
persistence is covered by ``tests/integration/test_command_evidence_repo.py``.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.command_evidence import (
    CommandExecutionEvidence,
    ExecutionVerdict,
    TerminalReason,
    bound_excerpt,
    compute_output_digest,
    derive_execution_verdict,
    derive_terminal_reason,
)
from app.modules.workspace.autonomous.command_evidence.recorder import compare_verdicts

# ---------------------------------------------------------------------------
# Terminal reason derivation
# ---------------------------------------------------------------------------


def test_terminal_reason_completed_when_exit_code_present():
    assert derive_terminal_reason(exit_code=0) is TerminalReason.COMPLETED
    assert derive_terminal_reason(exit_code=1) is TerminalReason.COMPLETED


def test_terminal_reason_crash_when_exit_code_none_with_result():
    assert derive_terminal_reason(exit_code=None, has_result=True) is TerminalReason.CRASH


def test_terminal_reason_missing_result_when_no_result():
    assert derive_terminal_reason(exit_code=0, has_result=False) is TerminalReason.MISSING_RESULT


def test_terminal_reason_timeout_takes_precedence_over_exit_code():
    assert derive_terminal_reason(exit_code=0, timed_out=True) is TerminalReason.TIMEOUT


def test_terminal_reason_cancelled_takes_precedence_over_signal():
    assert derive_terminal_reason(exit_code=0, signal=9, cancelled=True) is TerminalReason.CANCELLED


def test_terminal_reason_signal_when_signalled():
    assert derive_terminal_reason(exit_code=0, signal=15) is TerminalReason.SIGNAL


def test_timeout_cancel_signal_crash_and_missing_result_are_distinct():
    """#2046 acceptance: timeout/cancel/signal/crash/missing must be distinguishable."""
    reasons = {
        "timeout": derive_terminal_reason(exit_code=0, timed_out=True),
        "cancel": derive_terminal_reason(exit_code=0, cancelled=True),
        "signal": derive_terminal_reason(exit_code=0, signal=9),
        "crash": derive_terminal_reason(exit_code=None, has_result=True),
        "missing": derive_terminal_reason(exit_code=0, has_result=False),
        "completed": derive_terminal_reason(exit_code=0),
    }
    assert len(set(reasons.values())) == len(reasons)


# ---------------------------------------------------------------------------
# Structured verdict (facts, not prose)
# ---------------------------------------------------------------------------


def test_verdict_passed_only_on_completed_exit_zero():
    assert (
        derive_execution_verdict(exit_code=0, terminal_reason=TerminalReason.COMPLETED)
        == ExecutionVerdict.PASSED
    )


def test_verdict_failed_on_non_zero_exit():
    assert (
        derive_execution_verdict(exit_code=1, terminal_reason=TerminalReason.COMPLETED)
        == ExecutionVerdict.FAILED
    )


def test_verdict_failed_on_timeout():
    assert (
        derive_execution_verdict(exit_code=None, terminal_reason=TerminalReason.TIMEOUT)
        == ExecutionVerdict.FAILED
    )


def test_verdict_not_run_when_missing_result():
    assert (
        derive_execution_verdict(exit_code=None, terminal_reason=TerminalReason.MISSING_RESULT)
        == ExecutionVerdict.NOT_RUN
    )


def test_verdict_inconclusive_when_completed_but_no_exit_code():
    assert (
        derive_execution_verdict(exit_code=None, terminal_reason=TerminalReason.COMPLETED)
        == ExecutionVerdict.INCONCLUSIVE
    )


# ---------------------------------------------------------------------------
# Output digest / excerpt
# ---------------------------------------------------------------------------


def test_output_digest_stable_and_none_for_empty():
    assert compute_output_digest("") is None
    assert compute_output_digest("x") == compute_output_digest("x")


def test_bound_excerpt_truncates():
    long = "x" * 5000
    assert len(bound_excerpt(long)) == 4096
    assert bound_excerpt("short") == "short"


# ---------------------------------------------------------------------------
# Shadow comparison
# ---------------------------------------------------------------------------


def test_shadow_pass_without_evidence_is_divergence():
    """#2046/#1967 core: agent prose cannot mark pass without execution evidence."""
    result = compare_verdicts(heuristic_passed=True, evidence_rows=[])
    assert result["divergence"] is True
    assert result["structured_verdict"] == "not_run"


def test_shadow_no_pass_without_evidence_is_not_divergence():
    result = compare_verdicts(heuristic_passed=False, evidence_rows=[])
    assert result["divergence"] is False


def test_shadow_agreement_when_all_evidence_passed():
    rows = [
        CommandExecutionEvidence(command_id="c1", exit_code=0, terminal_reason="completed"),
        CommandExecutionEvidence(command_id="c2", exit_code=0, terminal_reason="completed"),
    ]
    result = compare_verdicts(heuristic_passed=True, evidence_rows=rows)
    assert result["divergence"] is False
    assert result["structured_verdict"] == "passed"


def test_shadow_divergence_when_heuristic_pass_but_evidence_failed():
    """The #1967 scenario: heuristic said pass, evidence says one command failed."""
    rows = [
        CommandExecutionEvidence(command_id="c1", exit_code=0, terminal_reason="completed"),
        CommandExecutionEvidence(command_id="c2", exit_code=1, terminal_reason="completed"),
    ]
    result = compare_verdicts(heuristic_passed=True, evidence_rows=rows)
    assert result["divergence"] is True
    assert result["structured_verdict"] == "failed"


def test_shadow_command_id_pairs_with_unique_terminal_state():
    """Each command_id maps to exactly one evidence row / verdict."""
    rows = [
        CommandExecutionEvidence(command_id="c1", exit_code=0, terminal_reason="completed"),
        CommandExecutionEvidence(command_id="c2", exit_code=None, terminal_reason="timeout"),
    ]
    result = compare_verdicts(heuristic_passed=False, evidence_rows=rows)
    assert result["structured_verdict"] == "failed"
    assert result["command_verdicts"] == ["passed", "failed"]
