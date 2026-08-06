"""Per-evidence framework gating + PASSED-only authority (#2376 PR-2).

Two coupled defects in the structured test gate. They must ship together: the
second currently *masks* the first, so fixing D2 alone would break healthy
python workflows on day one.

D4 — ``compute_run_verdict`` gates pytest superset coverage on the *run-level*
framework string. open-ace always infers ``"mixed"`` (it has both
``pyproject.toml`` and ``frontend/package.json``), so **every** evidence takes
the non-python branch — including pytest evidences that individually carry
``framework="python"`` and a populated ``coverage_scope``. The textbook flow
"targeted test fails -> fix -> full suite passes" therefore evaluates to FAILED:

    python -m pytest tests/test_a.py -q   exit 1
    python -m pytest tests/ -q            exit 0
        compute_run_verdict(..., "mixed")  -> FAILED
        compute_run_verdict(..., "python") -> PASSED

D2 — a structured FAILED sets ``structured_authoritative``, which zeroes both
``test_result_inconclusive`` and ``tests_actually_skipped``. Nothing downstream
reads ``tests_actually_run``, so a run whose tests demonstrably failed falls
through to "Tests passed" and opens a PR.

The fix makes only PASSED authoritative and lets FAILED fall back to the
heuristic, which is fail-closed. Collapsing the three-way branch to two arms is
load-bearing: leaving the ``elif FAILED: tests_actually_run = False`` arm in
place would pin a failed verdict to False without ever consulting the
heuristic, so a same-command fail-then-rerun-pass session (workflow 221's
iteration shape) would be killed.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    parse_test_evidence,
)
from app.modules.workspace.autonomous.command_evidence.test_verdict import compute_run_verdict
from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)


def _ce(row_id: int, command: str, exit_code: int, output: str = "") -> CommandExecutionEvidence:
    if not output:
        output = "1 failed, 2 passed" if exit_code else "5 passed"
    return CommandExecutionEvidence(
        command_id=f"tool_use_{row_id}",
        id=row_id,
        tool_name="Bash",
        shell_command=command,
        exit_code=exit_code,
        output_excerpt=output,
        session_id="s1",
    )


def _verdict(commands, framework: str) -> ExecutionVerdict:
    evidences = [parse_test_evidence(ce, framework_hint=framework) for ce in commands]
    return compute_run_verdict(evidences)


# --- D4: pytest scope coverage must not depend on the run-level string -------


def test_targeted_failure_covered_by_superset_pass_under_mixed():
    # The defining case. Both evidences carry framework="python" individually;
    # only the run-level hint says "mixed". Superset coverage must still apply.
    commands = [
        _ce(1, "python -m pytest tests/test_a.py -q", 1),
        _ce(2, "python -m pytest tests/ -q", 0),
    ]
    assert _verdict(commands, "mixed") is ExecutionVerdict.PASSED


def test_same_case_under_python_hint_is_unchanged():
    commands = [
        _ce(1, "python -m pytest tests/test_a.py -q", 1),
        _ce(2, "python -m pytest tests/ -q", 0),
    ]
    assert _verdict(commands, "python") is ExecutionVerdict.PASSED


def test_per_evidence_framework_drives_the_branch_not_the_hint():
    # Every evidence here parses as python regardless of the hint, so the
    # verdict must be identical across hints.
    commands = [
        _ce(1, "python -m pytest tests/test_a.py -q", 1),
        _ce(2, "python -m pytest tests/ -q", 0),
    ]
    assert _verdict(commands, "mixed") is _verdict(commands, "python")


def test_uncovered_pytest_failure_still_fails_under_mixed():
    # Superset coverage must not become a blanket amnesty: a narrower later run
    # does not cover a broader earlier failure.
    commands = [
        _ce(1, "python -m pytest tests/ -q", 1),
        _ce(2, "python -m pytest tests/test_a.py -q", 0),
    ]
    assert _verdict(commands, "mixed") is ExecutionVerdict.FAILED


def test_non_python_evidence_still_does_not_cross_cover_under_mixed():
    # Only pytest carries scope information, so two different shell commands
    # must not cover one another even when the run-level hint is "mixed".
    commands = [
        _ce(1, "bash tests/integration/a.sh", 1),
        _ce(2, "bash tests/integration/b.sh", 0),
    ]
    assert _verdict(commands, "mixed") is ExecutionVerdict.FAILED


def test_same_shell_command_rerun_stays_failed_at_the_structured_layer():
    # Documents an ACCEPTED limitation, not a bug. Non-pytest evidence is keyed
    # by the per-invocation command_id, so two runs of identical text do not
    # collapse and the structured verdict stays FAILED.
    #
    # This is safe only because of the PASSED-only change: FAILED is no longer
    # authoritative, so the gate falls back to `_has_passing_test_tool_result`,
    # whose own latest-wins is keyed on the normalized command and therefore
    # *does* collapse the rerun. That rescue is exercised in
    # test_gate_flags.py::test_conclusive_rerun_pass_supersedes_structured_failed.
    #
    # The command must be one the recognizer actually admits — a bare
    # `bash tests/x.sh` is not recognized until PR-3's Fix C, so using it here
    # would make the case unreachable in production AND break the rescue claim,
    # since the heuristic shares the same recognizer.
    #
    # Normalising the key here as well was considered and deliberately dropped:
    # it needs a new column on test_execution_evidence, and it opens a `| head`
    # exit-code masking hole the text-signal-gated heuristic does not have.
    commands = [
        _ce(1, "npm test", 1, "Tests: 1 failed"),
        _ce(2, "npm test", 0, "Tests: 40 passed"),
    ]
    assert _verdict(commands, "mixed") is ExecutionVerdict.FAILED


def test_stale_pass_cannot_cover_a_later_failure():
    # #1967 invariant: ordering matters. A pass *before* the failure must not
    # clear it.
    commands = [
        _ce(1, "python -m pytest tests/ -q", 0),
        _ce(2, "python -m pytest tests/test_a.py -q", 1),
    ]
    assert _verdict(commands, "mixed") is ExecutionVerdict.FAILED


# NOTE: the D2 half of #2376 (PASSED-only authority, the three-way collapse,
# the prose exclusion for FAILED, the fallback counter and the comment line) is
# gate behaviour, not verdict behaviour, so it is covered in
# tests/issues/2376/test_gate_flags.py against _run_test_phase itself. An
# assertion here that merely restated `verdict == PASSED` would be a tautology
# — it passes unmodified on main, where FAILED *is* authoritative.
