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
  each other; only an exact retry of the same command can).

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
) -> dict[tuple, tuple[str, _PytestScope | None, int]]:
    """Reduce to the latest verdict/scope/order per normalized command.

    ``authoritative`` is ordered by execution (the caller passes evidences in
    ``id`` order). When a command was invoked more than once, only the newest
    invocation's verdict survives — a stale pass from before a code change
    cannot satisfy an interrupted rerun (#1967).

    The scope is read per evidence, not gated on a run-level framework string
    (#2376 D4): a pytest evidence carries its own ``coverage_scope`` even when
    the project as a whole infers ``"mixed"``.
    """
    latest: dict[tuple, tuple[str, _PytestScope | None, int]] = {}
    for order, evidence in authoritative:
        key = _command_key(evidence)
        scope = (
            _scope_from_dict(evidence.coverage_scope) if evidence.framework == "python" else None
        )
        previous = latest.get(key)
        if previous is None or order > previous[2]:
            latest[key] = (evidence.verdict, scope, order)
    return latest


def _has_uncovered_failure(
    authoritative: list[tuple[int, TestExecutionEvidence]],
) -> bool:
    """Whether any command's latest failure lacks a later passing cover.

    A passing command covers an earlier failure only when it ran later and its
    scope is a provable superset. Exact retries of the same command are already
    collapsed by ``_latest_state``; this pass only handles cross-command
    superset coverage, which is pytest-only.

    Scope coverage needs no framework gate: ``_latest_state`` only records a
    scope for pytest evidence, and ``_pytest_scope_covers`` returns False when
    either side is None, so non-pytest commands can never cross-cover.
    """
    latest = _latest_state(authoritative)
    passing = [
        (scope, order)
        for verdict, scope, order in latest.values()
        if verdict == ExecutionVerdict.PASSED.value
    ]
    for verdict, failed_scope, order in latest.values():
        if verdict == ExecutionVerdict.PASSED.value:
            continue
        # Latest verdict for this command is FAILED (or non-pass). A later
        # passing command may clear it — but only for pytest, and only when
        # the passing scope provably covers the failed scope.
        covered = False
        for passing_scope, passing_order in passing:
            if passing_order <= order:
                continue
            if _pytest_scope_covers(passing_scope, failed_scope):
                covered = True
                break
        if not covered:
            return True
    return False


def compute_run_verdict(test_evidences: list[TestExecutionEvidence]) -> ExecutionVerdict:
    """Aggregate per-command evidence into a run-level verdict.

    Returns:
        - ``NOT_RUN`` — no test evidence recorded for the run.
        - ``INCONCLUSIVE`` — evidence exists but no HIGH/MEDIUM-confidence
          signal (all generic/LOW), or some test command could not be parsed
          while the rest passed. The gate falls back to the heuristic.
        - ``FAILED`` — at least one HIGH/MEDIUM command failed and no later
          passing superset covers it.
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

    if _has_uncovered_failure(authoritative):
        return ExecutionVerdict.FAILED

    if has_low:
        # No uncovered HIGH/MEDIUM failure, but at least one command was
        # unparseable (LOW). The structured layer cannot confirm it passed,
        # so the run is not yet authoritative-PASSED — fall back to heuristic.
        return ExecutionVerdict.INCONCLUSIVE

    return ExecutionVerdict.PASSED
