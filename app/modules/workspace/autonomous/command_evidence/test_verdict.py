"""Run-level test verdict from structured evidence (#2046 Phase B).

Domain module under ``app/`` (not a unit test — pytest collects only ``tests/``);
the ``test_`` prefix means *test-verdict*, not a test module.

``compute_run_verdict`` aggregates per-command :class:`TestExecutionEvidence`
into a single run-level :class:`ExecutionVerdict`. It re-implements the
coverage/override rules of the legacy heuristic
(``orchestrator._has_passing_test_tool_result``) but consumes structured
evidence instead of the raw ``event_log``:

- every distinct command must pass (any HIGH/MEDIUM FAILED → run FAILED,
  unless a later passing superset covers it);
- a later passing pytest superset clears an earlier failure for the same
  execution context (``_pytest_scope_covers``);
- a targeted pass never clears a failed full suite;
- the latest invocation of a command wins (stale pass cannot satisfy a rerun);
- non-pytest frameworks do not cross-cover (different commands cannot clear
  each other; only an exact retry of the same command can) — but that is
  *undecidable*, not a failure: with no scope to compare, a later pass yields
  INCONCLUSIVE so the heuristic decides. Decided non-coverage (both pytest
  scopes known, the later pass narrower) still yields FAILED (#2376 PR-2).
  #2665 carve-out: a later pass carrying no test semantics at all (count-less
  scope-less exit-0 — lint/format) cannot make a structurally-parsed test
  failure undecidable; see ``_has_test_semantics``.

Input source of truth: ``TestExecutionEvidence`` rows, never agent prose.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.command_evidence.scope import (
    _pytest_scope_covers,
    _PytestScope,
)
from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    ParserConfidence,
    TestExecutionEvidence,
    _scope_from_dict,
)
from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict

_AUTHORITATIVE = (ParserConfidence.HIGH.value, ParserConfidence.MEDIUM.value)


def _command_key(evidence: TestExecutionEvidence) -> tuple:
    """Stable identity for grouping reruns of the same command.

    For pytest, the (context, selectors) pair from ``coverage_scope`` is the
    normalized identity — ``pytest a b | head`` and ``pytest a b | tail``
    share it, so a truncated exploration run and its later rerun collapse to
    one command. For other frameworks (or commands too complex to scope), the
    raw ``command_id`` is the identity (each invocation is distinct).

    Gated on the evidence's *own* framework, not the run-level hint (#2376 D4).
    A polyglot repo infers ``"mixed"`` at the project level, and gating on that
    string sent pytest evidences — which individually carry
    ``framework="python"`` and a populated ``coverage_scope`` — down the
    non-python branch, disabling superset coverage entirely.
    """
    if evidence.framework == "python" and evidence.coverage_scope:
        context = evidence.coverage_scope.get("context")
        selectors = frozenset(evidence.coverage_scope.get("selectors") or [])
        if isinstance(context, str):
            return ("pytest", context, selectors)
    return ("cmd", evidence.command_id)


def _latest_state(
    authoritative: list[tuple[int, TestExecutionEvidence]],
) -> dict[tuple, tuple[str, _PytestScope | None, int, TestExecutionEvidence]]:
    """Reduce to the latest verdict/scope/order per normalized command.

    ``authoritative`` is ordered by execution (the caller passes evidences in
    ``id`` order). When a command was invoked more than once, only the newest
    invocation's verdict survives — a stale pass from before a code change
    cannot satisfy an interrupted rerun (#1967).

    The scope is read per evidence, not gated on a run-level framework string
    (#2376 D4): a pytest evidence carries its own ``coverage_scope`` even when
    the project as a whole infers ``"mixed"``. The evidence itself rides along
    so coverer selection can apply ``_has_test_semantics`` (#2665).
    """
    latest: dict[tuple, tuple[str, _PytestScope | None, int, TestExecutionEvidence]] = {}
    for order, evidence in authoritative:
        key = _command_key(evidence)
        scope = (
            _scope_from_dict(evidence.coverage_scope) if evidence.framework == "python" else None
        )
        previous = latest.get(key)
        if previous is None or order > previous[2]:
            latest[key] = (evidence.verdict, scope, order, evidence)
    return latest


def _has_test_semantics(evidence: TestExecutionEvidence) -> bool:
    """Whether the evidence structurally proves a TEST command ran.

    True only when the parse produced pytest scope/counts/selectors or parsed
    framework counts (jest/cargo set ``passed``; go never records counts).
    False for the exit-0 fallback arms — ``_parse_generic`` on any clean
    command, ``_parse_pytest``'s exit-0-unparseable-output arm (which claims
    ``parser="pytest"``!), and the framework parsers' own exit-0 arms on
    non-test invocations of the same tool family (``go vet``/``go build``,
    ``cargo clippy``/``cargo fmt``) — all of which yield a count-less,
    scope-less MEDIUM pass indistinguishable from lint/format commands
    (#2665). The paired guard in ``_classify_failures`` still preserves the
    #2376 deferral when the failing side is equally unstructured (a pure
    ``go test`` fail + ``go test`` pass pair carries no counts on either
    side, so neither has test semantics and the defer stands).
    """
    return (
        evidence.coverage_scope is not None
        or evidence.collected is not None
        or evidence.passed is not None
        or evidence.failed is not None
        or evidence.skipped is not None
        or evidence.errors is not None
        or bool(evidence.selectors)
    )


def _classify_failures(authoritative: list[tuple[int, TestExecutionEvidence]]) -> str:
    """Classify a run's failures as ``none`` / ``unresolved`` / ``uncertain``.

    A passing command covers an earlier failure only when it ran later and its
    scope is a provable superset. Exact retries are already collapsed by
    ``_latest_state``; this pass handles cross-command coverage, which is
    pytest-only — ``_latest_state`` records a scope only for pytest evidence and
    ``_pytest_scope_covers`` rejects a ``None`` side.

    The three-way split matters because provable coverage is far rarer than it
    looks (#2376 PR-2 review). ``_pytest_test_scope`` bails on any option it does
    not model, so this repo's own CI command —
    ``pytest tests/ -v --cov=app --cov-fail-under=30`` — yields no scope, and no
    non-pytest runner ever does. Collapsing "a later command passed but we cannot
    prove it covers this failure" into FAILED would hard-fail the ordinary
    fix-then-rerun-broader flow for vitest, go, cargo and most real pytest
    invocations. The evidence does not establish a failing run there, so the
    caller defers to the heuristic instead of asserting one.

    Returns:
        ``none``       — every latest verdict passed, or each failure is covered.
        ``unresolved`` — a failure with no later passing command at all.
        ``uncertain``  — a failure followed by a passing command whose coverage
                         cannot be proven.
    """
    latest = _latest_state(authoritative)
    passing = [
        (scope, order, evidence)
        for verdict, scope, order, evidence in latest.values()
        if verdict == ExecutionVerdict.PASSED.value
    ]
    result = "none"
    for verdict, failed_scope, order, failed_evidence in latest.values():
        if verdict == ExecutionVerdict.PASSED.value:
            continue
        covered = False
        undecidable = False
        for passing_scope, passing_order, passing_evidence in passing:
            if passing_order <= order:
                continue
            if _pytest_scope_covers(passing_scope, failed_scope):
                covered = True
                break
            # Both scopes known means non-coverage was *decided* (a targeted
            # pass genuinely does not clear a failed broader run — #1967). Only
            # a missing scope on either side leaves it undecidable…
            if passing_scope is None or failed_scope is None:
                # …unless the failure is structurally a TEST failure (parsed
                # scope/counts) while the passing command carries no test
                # semantics at all (#2665). pre-commit/black/ruff exit-0 pass
                # through the same fallback arms, and letting them soften a
                # decisive pytest "3 failed, 24 passed" into "uncertain" →
                # INCONCLUSIVE spun #2590's workflow in retry loops instead of
                # the tests-failed → dev-fix loop. Both-generic runs (bash
                # exit-code scripts) keep the #2376 PR-2 deferral.
                if _has_test_semantics(failed_evidence) and not _has_test_semantics(
                    passing_evidence
                ):
                    continue
                undecidable = True
        if covered:
            continue
        if not undecidable:
            return "unresolved"
        result = "uncertain"
    return result


def compute_run_verdict(test_evidences: list[TestExecutionEvidence]) -> ExecutionVerdict:
    """Aggregate per-command evidence into a run-level verdict.

    Returns:
        - ``NOT_RUN`` — no test evidence recorded for the run.
        - ``INCONCLUSIVE`` — evidence exists but no HIGH/MEDIUM-confidence
          signal (all generic/LOW); or some test command could not be parsed
          while the rest passed; or a failure is followed by a passing command
          whose coverage cannot be decided. The gate falls back to the heuristic.
        - ``FAILED`` — at least one HIGH/MEDIUM command failed with either no
          later passing command at all, or a later pass that is *decidably* not
          a superset.
        - ``PASSED`` — every HIGH/MEDIUM command passed (or its failure was
          covered by a later passing superset) and no unparseable command
          leaves the run unconfirmable.

    Coverage rules are derived per evidence, not from a project-level framework
    hint. Threading that hint down here is exactly what caused #2376 D4: a
    polyglot repo infers ``"mixed"``, which sent pytest evidences down the
    non-python branch and disabled superset coverage for the whole run. Non-pytest
    evidence still never cross-covers, because only ``_parse_pytest`` emits a
    ``coverage_scope`` and ``_pytest_scope_covers`` rejects a ``None`` side.
    """
    if not test_evidences:
        return ExecutionVerdict.NOT_RUN

    has_low = any(e.parser_confidence == ParserConfidence.LOW.value for e in test_evidences)
    authoritative = [
        (index, evidence)
        for index, evidence in enumerate(test_evidences)
        if evidence.parser_confidence in _AUTHORITATIVE
    ]

    if not authoritative:
        # Every test command was generic/LOW — cannot authoritatively judge.
        return ExecutionVerdict.INCONCLUSIVE

    failures = _classify_failures(authoritative)
    if failures == "unresolved":
        return ExecutionVerdict.FAILED
    if failures == "uncertain":
        # A failure followed by a later passing command we cannot prove covers
        # it. Asserting FAILED would hard-fail the ordinary fix-then-rerun flow
        # for every non-pytest runner and for pytest invocations carrying
        # options the scope parser does not model (#2376 PR-2 review).
        return ExecutionVerdict.INCONCLUSIVE

    if has_low:
        # No uncovered HIGH/MEDIUM failure, but at least one command was
        # unparseable (LOW). The structured layer cannot confirm it passed,
        # so the run is not yet authoritative-PASSED — fall back to heuristic.
        return ExecutionVerdict.INCONCLUSIVE

    return ExecutionVerdict.PASSED
