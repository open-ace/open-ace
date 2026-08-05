"""Contract tests for the run-level structured test verdict (#2046 Phase B).

Migrates the 12 coverage/override rules from ``test_autonomous_ci_guardrails``
``test_test_evidence_*`` to consume ``TestExecutionEvidence`` instead of the
raw ``event_log``. Each rule encodes an incident from #1967 / the #1998
reliability series.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    ParserConfidence,
    TestExecutionEvidence,
)
from app.modules.workspace.autonomous.command_evidence.test_verdict import compute_run_verdict
from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict

_CTX = "python -m pytest"


def _te(
    command_id: str,
    verdict: str,
    *,
    confidence: str = ParserConfidence.HIGH.value,
    selectors: list[str] | None = None,
    context: str = _CTX,
    framework: str = "python",
) -> TestExecutionEvidence:
    """Build a TestExecutionEvidence with an explicit scope for verdict tests."""
    coverage_scope = None
    if framework == "python":
        coverage_scope = {"context": context, "selectors": sorted(selectors or [])}
    return TestExecutionEvidence(
        command_id=command_id,
        framework=framework,
        verdict=verdict,
        parser_confidence=confidence,
        selectors=selectors or [],
        coverage_scope=coverage_scope,
    )


def _run_failed(evidences: list[TestExecutionEvidence], framework: str = "python") -> bool:
    return compute_run_verdict(evidences, framework) == ExecutionVerdict.FAILED


# ── coverage / override rules (incident-encoding) ─────────────────────────────


def test_every_distinct_command_must_pass():
    # A second command that passed does not erase a first command's failure.
    evidences = [
        _te("full", ExecutionVerdict.FAILED.value, selectors=["."]),  # full suite failed
        _te("one", ExecutionVerdict.PASSED.value, selectors=["tests/test_one.py"]),
    ]
    assert _run_failed(evidences)


def test_targeted_pass_does_not_cover_failed_full_suite_1967():
    # full suite (empty selector set) failed; a targeted pass cannot clear it.
    evidences = [
        _te("full", ExecutionVerdict.FAILED.value, selectors=[]),  # full suite
        _te("one", ExecutionVerdict.PASSED.value, selectors=["tests/test_one.py"]),
    ]
    assert _run_failed(evidences)


def test_later_passing_superset_clears_earlier_failure():
    # A later run of a provable superset clears the earlier failure.
    evidences = [
        _te("group", ExecutionVerdict.FAILED.value, selectors=["tests/a.py", "tests/b.py"]),
        _te(
            "superset",
            ExecutionVerdict.PASSED.value,
            selectors=["tests/a.py", "tests/b.py", "tests/c.py"],
        ),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.PASSED


def test_earlier_passing_superset_does_not_clear_later_failure():
    # Order matters: a pass that came BEFORE a later failure cannot clear it.
    evidences = [
        _te(
            "superset",
            ExecutionVerdict.PASSED.value,
            selectors=["tests/a.py", "tests/b.py", "tests/c.py"],
        ),
        _te("group", ExecutionVerdict.FAILED.value, selectors=["tests/a.py", "tests/b.py"]),
    ]
    assert _run_failed(evidences)


def test_different_execution_context_does_not_cover():
    # Different python wrapper → different collection; cannot cross-cover.
    evidences = [
        _te(
            "a",
            ExecutionVerdict.FAILED.value,
            selectors=["tests/x.py"],
            context="python3.11 -m pytest",
        ),
        _te(
            "b",
            ExecutionVerdict.PASSED.value,
            selectors=["tests/x.py"],
            context="python3.12 -m pytest",
        ),
    ]
    assert _run_failed(evidences)


def test_same_command_latest_invocation_wins_head_tail_1967():
    # head run (pass, no summary) then tail rerun (pass, summary) → run passes.
    # Models the truncated-exploration + rerun pattern from #1967.
    evidences = [
        _te(
            "head",
            ExecutionVerdict.PASSED.value,
            selectors=["tests/x.py"],
            confidence=ParserConfidence.MEDIUM.value,
        ),
        _te("tail", ExecutionVerdict.PASSED.value, selectors=["tests/x.py"]),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.PASSED


def test_stale_pass_does_not_satisfy_rerun_that_failed():
    # Same scope: earlier pass then later failure → the failure is the latest.
    evidences = [
        _te("first", ExecutionVerdict.PASSED.value, selectors=["tests/x.py"]),
        _te("second", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
    ]
    assert _run_failed(evidences)


def test_non_pytest_does_not_cross_cover_between_commands():
    # jest/go/cargo: a pass on one command cannot clear a failure on another.
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, framework="javascript"),
        _te("c2", ExecutionVerdict.PASSED.value, framework="javascript"),
    ]
    assert _run_failed(evidences, "javascript")


def test_restricted_pass_does_not_cover_earlier_failure():
    # Subset pass after a wider failure does not cover (not a superset).
    evidences = [
        _te("wide", ExecutionVerdict.FAILED.value, selectors=["tests/a.py", "tests/b.py"]),
        _te("narrow", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
    ]
    assert _run_failed(evidences)


# ── verdict aggregation / fallback boundary ───────────────────────────────────


def test_all_passed_returns_passed():
    evidences = [
        _te("c1", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
        _te("c2", ExecutionVerdict.PASSED.value, selectors=["tests/b.py"]),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.PASSED


def test_empty_evidence_is_not_run():
    assert compute_run_verdict([], "python") == ExecutionVerdict.NOT_RUN


def test_all_low_confidence_is_inconclusive():
    # Parser could not parse any command — defer to the heuristic fallback.
    evidences = [
        _te("c1", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.INCONCLUSIVE


def test_low_confidence_command_makes_run_inconclusive_when_rest_pass():
    # One command parsed-passed, another was unparseable (LOW). The structured
    # layer cannot confirm the LOW command passed → fall back to heuristic.
    evidences = [
        _te("c1", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
        _te("c2", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.INCONCLUSIVE


def test_failed_takes_priority_over_inconclusive_peer():
    # A real HIGH-confidence failure must not be masked by an unparseable peer.
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, selectors=["tests/a.py"]),
        _te("c2", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences, "python") == ExecutionVerdict.FAILED


def test_medium_confidence_failure_blocks_pass():
    # MEDIUM (exit-code only) failure still counts as a real failure.
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, confidence=ParserConfidence.MEDIUM.value),
        _te("c2", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
    ]
    assert _run_failed(evidences)
