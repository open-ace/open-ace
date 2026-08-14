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
    passed: int | None = None,
    failed: int | None = None,
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
        passed=passed,
        failed=failed,
    )


def _run_failed(evidences: list[TestExecutionEvidence], framework: str = "python") -> bool:
    return compute_run_verdict(evidences) == ExecutionVerdict.FAILED


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
    assert compute_run_verdict(evidences) == ExecutionVerdict.PASSED


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
    assert compute_run_verdict(evidences) == ExecutionVerdict.PASSED


def test_stale_pass_does_not_satisfy_rerun_that_failed():
    # Same scope: earlier pass then later failure → the failure is the latest.
    evidences = [
        _te("first", ExecutionVerdict.PASSED.value, selectors=["tests/x.py"]),
        _te("second", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
    ]
    assert _run_failed(evidences)


def test_non_pytest_cross_command_pass_is_undecidable_not_a_failure():
    # jest/go/cargo carry no coverage scope, so a pass on one command still
    # cannot *clear* a failure on another — but neither can the evidence assert
    # the run failed. #2376 PR-2 split those: the verdict is INCONCLUSIVE, which
    # defers to the heuristic, rather than FAILED.
    #
    # This changed because PR-2 made FAILED actually block the phase. While it
    # was inert, calling this case FAILED was harmless; once it blocks, it
    # hard-fails the ordinary "targeted test fails -> fix -> broader run passes"
    # flow for every non-pytest runner. Decided non-coverage (both scopes known,
    # pytest) still yields FAILED — see
    # test_targeted_pass_does_not_cover_failed_full_suite_1967.
    # (Counts made explicit post-#2665: a pass that never parsed any test
    # output has no test semantics at all — see
    # test_lint_pass_after_pytest_failure_stays_failed_2665.)
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, framework="javascript", passed=2, failed=1),
        _te("c2", ExecutionVerdict.PASSED.value, framework="javascript", passed=3),
    ]
    assert not _run_failed(evidences, "javascript")
    assert compute_run_verdict(evidences) is ExecutionVerdict.INCONCLUSIVE


def _lint_pass(command_id: str, *, parser: str = "pytest") -> TestExecutionEvidence:
    """A count-less exit-0 lint/format command (pre-commit/black/ruff style).

    In a python-hinted project these route through ``_parse_pytest`` whose
    exit-0-unparseable arm emits ``parser="pytest"`` with NO counts and NO
    coverage scope (the ACTUAL prod shape of #2665's evidence rows);
    ``_parse_generic``'s exit-0 arm is the parser="generic" twin. Lock BOTH
    shapes — a future "pytest parser ⇒ has test semantics" shortcut must not
    silently reintroduce the bug.
    """
    return TestExecutionEvidence(
        command_id=command_id,
        framework="python",
        verdict=ExecutionVerdict.PASSED.value,
        parser_confidence=ParserConfidence.MEDIUM.value,
        parser=parser,
        selectors=[],
        coverage_scope=None,
    )


def test_lint_pass_after_pytest_failure_stays_failed_2665():
    # pre-commit/black/ruff exiting 0 after a DECISIVE pytest failure (3
    # failed, 24 passed) must not defuse the verdict. Before #2665 the bare
    # pass's None scope hit the undecidable branch ("uncertain" →
    # INCONCLUSIVE), so the workflow retried forever instead of entering the
    # productive tests-failed → dev-fix loop (#2590's workflow, verified
    # against prod evidence rows). Both lint shapes locked: parser="pytest"
    # (the real prod shape via _parse_pytest's exit-0 arm) and "generic".
    for parser in ("pytest", "generic"):
        evidences = [
            _te("t1", ExecutionVerdict.PASSED.value, selectors=["tests/x.py::test_a"]),
            _te("t2", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
            _lint_pass("lint1", parser=parser),
        ]
        assert compute_run_verdict(evidences) is ExecutionVerdict.FAILED, parser


def test_rerun_with_truncated_output_after_failure_is_failed_2665():
    # Documented trade-off: a legitimate fix-then-rerun whose passing rerun is
    # count-less AND scope-less (truncated output_excerpt ate the only summary
    # line, or an unmodeled wrapper like `make test`) is now FAILED where it
    # previously deferred to the heuristic. The evidence is indistinguishable
    # from a lint command, so decisive beats deferred — the failure routes the
    # workflow into the dev-fix loop, which re-runs tests anyway.
    evidences = [
        _te("t1", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
        _lint_pass("rerun"),
    ]
    assert compute_run_verdict(evidences) is ExecutionVerdict.FAILED


def test_counted_pass_after_failure_stays_uncertain_2665():
    # A pass WITH parsed framework counts (reachable shape: _parse_cargo on a
    # "test result: ok. 5 passed" rerun) keeps the #2376 PR-2 undecidable
    # semantics — only count-less/scope-less passes are stripped of coverer
    # status.
    counted = TestExecutionEvidence(
        command_id="g1",
        framework="rust",
        verdict=ExecutionVerdict.PASSED.value,
        parser_confidence=ParserConfidence.HIGH.value,
        parser="cargo",
        selectors=[],
        coverage_scope=None,
        passed=5,
    )
    evidences = [
        _te("t1", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
        counted,
    ]
    assert compute_run_verdict(evidences) is ExecutionVerdict.INCONCLUSIVE


def test_go_vet_pass_after_go_test_failure_does_not_defuse_2665():
    # Framework parsers' exit-0 arms are NOT test semantics: `go vet` (or
    # `cargo clippy`) exiting 0 after a go/cargo test failure must not soften
    # the failure. The failing go evidence is itself count-less (no test
    # semantics), so the defer survives only between two STRUCTURED pairs —
    # here the failure is structured (pytest counts) and the vet pass is not.
    vet_pass = TestExecutionEvidence(
        command_id="v1",
        framework="go",
        verdict=ExecutionVerdict.PASSED.value,
        parser_confidence=ParserConfidence.MEDIUM.value,
        parser="go_test",
        selectors=[],
        coverage_scope=None,
    )
    evidences = [
        _te("t1", ExecutionVerdict.FAILED.value, selectors=["tests/x.py"]),
        vet_pass,
    ]
    assert compute_run_verdict(evidences) is ExecutionVerdict.FAILED


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
    assert compute_run_verdict(evidences) == ExecutionVerdict.PASSED


def test_empty_evidence_is_not_run():
    assert compute_run_verdict([]) == ExecutionVerdict.NOT_RUN


def test_all_low_confidence_is_inconclusive():
    # Parser could not parse any command — defer to the heuristic fallback.
    evidences = [
        _te("c1", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences) == ExecutionVerdict.INCONCLUSIVE


def test_low_confidence_command_makes_run_inconclusive_when_rest_pass():
    # One command parsed-passed, another was unparseable (LOW). The structured
    # layer cannot confirm the LOW command passed → fall back to heuristic.
    evidences = [
        _te("c1", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
        _te("c2", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences) == ExecutionVerdict.INCONCLUSIVE


def test_failed_takes_priority_over_inconclusive_peer():
    # A real HIGH-confidence failure must not be masked by an unparseable peer.
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, selectors=["tests/a.py"]),
        _te("c2", ExecutionVerdict.INCONCLUSIVE.value, confidence=ParserConfidence.LOW.value),
    ]
    assert compute_run_verdict(evidences) == ExecutionVerdict.FAILED


def test_medium_confidence_failure_blocks_pass():
    # MEDIUM (exit-code only) failure still counts as a real failure.
    evidences = [
        _te("c1", ExecutionVerdict.FAILED.value, confidence=ParserConfidence.MEDIUM.value),
        _te("c2", ExecutionVerdict.PASSED.value, selectors=["tests/a.py"]),
    ]
    assert _run_failed(evidences)
