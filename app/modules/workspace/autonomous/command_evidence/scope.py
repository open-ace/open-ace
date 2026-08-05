"""Test-command scope helpers shared by the heuristic and structured gates.

Extracted from ``orchestrator`` (#2046 Phase B) so both the legacy heuristic
(``orchestrator._has_passing_test_tool_result``) and the structured verdict
(``test_verdict.compute_run_verdict``) reuse the same scope-comparison rules
without a circular import — the orchestrator imports the verdict module for
the gate, so the verdict module must not import back into the orchestrator.

A test command is normalized into a stable identity (stripping output-only
``head``/``tail`` pipelines) and a pytest "scope" (execution context +
selectors) so a later passing superset can clear an earlier failure, but a
targeted pass or a different runtime environment never can (#1967, #2046 §2).
"""

from __future__ import annotations

import os
import re
import shlex

_TEST_OUTPUT_FILTER_RE = re.compile(
    r"(?:\s+2>\&1)?\s*\|\s*(?:head|tail)(?:\s+-[^\s]+|\s+\d+)*\s*$",
    re.IGNORECASE,
)


def _normalize_test_command(command: str) -> str:
    """Return a stable identity for a test command across output-only filters.

    Autonomous agents commonly run ``pytest ... | head -100`` while exploring
    a failure and rerun the same target as ``pytest ... | tail -20`` after the
    fix.  Those filters change only which output is displayed, not the tests
    executed.  Treating the two strings as distinct left the truncated first
    run permanently inconclusive even when the later rerun passed.

    Strip only trailing ``head``/``tail`` pipelines (and their adjacent stderr
    merge).  Execution-affecting shell operators such as ``&&``/``||`` and
    pytest selectors/options remain part of the identity.
    """
    normalized = " ".join(str(command or "").split())
    while True:
        stripped = _TEST_OUTPUT_FILTER_RE.sub("", normalized).strip()
        if stripped == normalized:
            break
        normalized = stripped
    return re.sub(r"\s+2>\&1\s*$", "", normalized).strip()


_PytestScope = tuple[str, frozenset[str]]


def _pytest_test_scope(command: str) -> _PytestScope | None:
    """Return the pytest execution context and selectors when safely comparable.

    ``None`` means the command is too complex for safe scope comparison.  An
    empty selector set means a full-suite invocation.  This lets a later
    passing superset rerun clear earlier failures for the same files while
    ensuring a targeted pass or a different Python environment can never clear
    a failed full-suite command.
    """
    normalized = _normalize_test_command(command)
    if any(operator in normalized for operator in ("&&", "||", ";", "|")):
        return None
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return None

    pytest_index = -1
    for index, token in enumerate(tokens):
        if os.path.basename(token) in {"pytest", "py.test"}:
            pytest_index = index
            break
    if pytest_index < 0:
        return None

    # Only compare scopes when the invocation prefix has ordinary pytest
    # semantics.  Environment assignments and wrappers can change collection
    # even when the visible selectors are identical.
    prefix = tokens[:pytest_index]
    if prefix:
        python_name = os.path.basename(prefix[0])
        if (
            len(prefix) != 2
            or prefix[1] != "-m"
            or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", python_name) is None
        ):
            return None
        execution_context = f"{prefix[0]} -m {tokens[pytest_index]}"
    else:
        execution_context = tokens[pytest_index]

    scope_narrowing_options = {
        "-k",
        "-m",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--nf",
        "--new-first",
        "--sw",
        "--stepwise",
        "--stepwise-skip",
    }
    safe_flags = {
        "-q",
        "--quiet",
        "-v",
        "-vv",
        "--verbose",
        "-s",
        "-x",
        "--exitfirst",
        "--disable-warnings",
        "--strict-config",
        "--strict-markers",
        "--continue-on-collection-errors",
        "--full-trace",
        "--showlocals",
        "-l",
        "--no-header",
        "--no-summary",
    }
    safe_value_options = {
        "--tb",
        "--color",
        "--capture",
        "--durations",
        "--durations-min",
        "--junitxml",
        "--junit-prefix",
        "--basetemp",
        "--verbosity",
        "--maxfail",
    }

    selectors: set[str] = set()
    args = tokens[pytest_index + 1 :]
    index = 0
    selectors_only = False
    while index < len(args):
        token = args[index]
        if token == "--":
            selectors_only = True
            index += 1
            continue
        if not selectors_only and token.startswith("-"):
            option_name = token.split("=", 1)[0]
            if option_name in scope_narrowing_options:
                return None
            if token in safe_flags:
                index += 1
                continue
            if option_name in safe_value_options:
                if "=" not in token:
                    index += 1
                    if index >= len(args):
                        return None
                index += 1
                continue
            # Plugin and future pytest options are unknown here.  Exact-command
            # retries remain supported, but cross-command scope coverage is not.
            return None
        selectors.add(token.rstrip("/") or ".")
        index += 1

    return execution_context, frozenset(selectors)


def _pytest_scope_covers(
    passing_scope: _PytestScope | None,
    earlier_scope: _PytestScope | None,
) -> bool:
    """Whether a passing pytest command covers an earlier command's scope."""
    if passing_scope is None or earlier_scope is None:
        return False
    passing_context, passing_selectors = passing_scope
    earlier_context, earlier_selectors = earlier_scope
    if passing_context != earlier_context:
        return False
    if not passing_selectors:
        return True
    if not earlier_selectors:
        return False

    def _selector_covers(passing: str, earlier: str) -> bool:
        if passing == earlier:
            return True
        if passing in {".", "./"}:
            return True
        passing_path = passing.split("::", 1)[0].rstrip("/")
        earlier_path = earlier.split("::", 1)[0].rstrip("/")
        if "::" not in passing and passing_path == earlier_path:
            return True
        return "::" not in passing and earlier_path.startswith(f"{passing_path}/")

    return all(
        any(_selector_covers(passing, earlier) for passing in passing_selectors)
        for earlier in earlier_selectors
    )
