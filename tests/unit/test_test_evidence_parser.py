"""Contract tests for the structured test-evidence parsers (#2046 Phase B).

Covers per-framework output parsing, confidence grading, selector/scope
extraction, and dispatch. These parsers replace the agent-prose heuristics
as the authoritative source of pass/fail (#1967).
"""

from __future__ import annotations

from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    ParserConfidence,
    parse_test_evidence,
)
from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)


def _ce(
    command_id: str,
    shell: str,
    exit_code: int | None,
    excerpt: str,
    *,
    tool_name: str = "Bash",
) -> CommandExecutionEvidence:
    """Build a command evidence row the way Phase A persists it."""
    return CommandExecutionEvidence(
        command_id=command_id,
        session_id="sess-1",
        workflow_id="wf-1",
        milestone_id="ms-1",
        tool_name=tool_name,
        shell_command=shell,
        exit_code=exit_code,
        output_excerpt=excerpt,
    )


# ── pytest ───────────────────────────────────────────────────────────────────


def test_pytest_parser_extracts_passed_counts_and_high_confidence():
    te = parse_test_evidence(
        _ce("c1", "python -m pytest tests/test_a.py", 0, "3 passed in 0.45s"), "python"
    )
    assert te.framework == "python"
    assert te.parser == "pytest"
    assert te.passed == 3
    assert te.failed is None
    assert te.verdict == ExecutionVerdict.PASSED.value
    assert te.parser_confidence == ParserConfidence.HIGH.value


def test_pytest_parser_detects_failure_from_counts():
    te = parse_test_evidence(
        _ce("c1", "python -m pytest tests/test_b.py", 1, "1 failed, 2 passed in 0.5s"), "python"
    )
    assert te.failed == 1
    assert te.passed == 2
    assert te.verdict == ExecutionVerdict.FAILED.value
    assert te.parser_confidence == ParserConfidence.HIGH.value


def test_pytest_parser_extracts_selectors_and_coverage_scope():
    te = parse_test_evidence(
        _ce("c1", "python -m pytest tests/test_a.py tests/test_b.py -v", 0, "2 passed"), "python"
    )
    assert te.selectors == ["tests/test_a.py", "tests/test_b.py"]
    assert te.coverage_scope is not None
    assert te.coverage_scope["context"] == "python -m pytest"
    assert set(te.coverage_scope["selectors"]) == {"tests/test_a.py", "tests/test_b.py"}


def test_pytest_parser_chinese_summary_is_parsed():
    te = parse_test_evidence(_ce("c1", "pytest", 0, "通过: 2398 个, 失败: 0 个"), "python")
    assert te.passed == 2398
    assert te.verdict == ExecutionVerdict.PASSED.value


def test_pytest_parser_unittest_ok_synthesizes_pass_count():
    excerpt = "Ran 4 tests in 0.1s\n\nOK\n"
    te = parse_test_evidence(_ce("c1", "python -m unittest discover", 0, excerpt), "python")
    assert te.passed == 4
    assert te.verdict == ExecutionVerdict.PASSED.value


def test_pytest_parser_clean_exit_without_summary_is_medium_pass():
    # Saw a real exit code but no parseable pytest summary line.
    te = parse_test_evidence(
        _ce("c1", "python -m pytest tests/x.py", 0, "=== test session starts ==="), "python"
    )
    assert te.verdict == ExecutionVerdict.PASSED.value
    assert te.parser_confidence == ParserConfidence.MEDIUM.value


# ── jest / go / cargo ─────────────────────────────────────────────────────────


def test_jest_parser_extracts_counts():
    te = parse_test_evidence(_ce("c1", "npm test", 0, "Tests: 5 passed, 0 failed"), "javascript")
    assert te.framework == "javascript"
    assert te.parser == "jest"
    assert te.passed == 5
    assert te.verdict == ExecutionVerdict.PASSED.value
    assert te.parser_confidence == ParserConfidence.HIGH.value


def test_go_parser_ok_is_passed():
    te = parse_test_evidence(_ce("c1", "go test ./...", 0, "ok  github.com/acme/pkg  0.12s"), "go")
    assert te.framework == "go"
    assert te.verdict == ExecutionVerdict.PASSED.value
    assert te.parser_confidence == ParserConfidence.HIGH.value


def test_go_parser_fail_is_failed():
    te = parse_test_evidence(
        _ce("c1", "go test ./...", 1, "FAIL  github.com/acme/pkg  0.12s"), "go"
    )
    assert te.verdict == ExecutionVerdict.FAILED.value


def test_cargo_parser_extracts_counts():
    te = parse_test_evidence(
        _ce("c1", "cargo test", 0, "test result: ok. 4 passed; 0 failed"), "rust"
    )
    assert te.framework == "rust"
    assert te.passed == 4
    assert te.verdict == ExecutionVerdict.PASSED.value


# ── generic fallback ──────────────────────────────────────────────────────────


def test_generic_parser_exit_zero_with_output_is_medium_pass():
    te = parse_test_evidence(_ce("c1", "make test", 0, "building...\nall good"), "generic")
    assert te.framework == "generic"
    assert te.parser == "generic"
    assert te.verdict == ExecutionVerdict.PASSED.value
    assert te.parser_confidence == ParserConfidence.MEDIUM.value


def test_generic_parser_non_zero_exit_is_failed():
    te = parse_test_evidence(_ce("c1", "make test", 2, "make: *** [test] Error 2"), "generic")
    assert te.verdict == ExecutionVerdict.FAILED.value
    assert te.parser_confidence == ParserConfidence.MEDIUM.value


def test_generic_parser_missing_exit_code_is_inconclusive_low():
    # No exit code, no framework signal — cannot authoritatively judge.
    te = parse_test_evidence(_ce("c1", "make test", None, "building..."), "generic")
    assert te.verdict == ExecutionVerdict.INCONCLUSIVE.value
    assert te.parser_confidence == ParserConfidence.LOW.value


# ── dispatch / framework resolution ───────────────────────────────────────────


def test_dispatch_uses_command_signal_over_hint():
    # command says pytest but hint says javascript — command wins.
    te = parse_test_evidence(_ce("c1", "pytest tests/x.py", 0, "2 passed"), "javascript")
    assert te.framework == "python"
    assert te.parser == "pytest"


def test_dispatch_falls_back_to_hint_when_command_has_no_signal():
    te = parse_test_evidence(_ce("c1", "./bin/run-tests", 0, "ok"), "go")
    # No command signal → use hint. go parser sees no "ok pkg" line → medium pass.
    assert te.framework == "go"


def test_dispatch_falls_back_to_generic_when_hint_empty():
    te = parse_test_evidence(_ce("c1", "./bin/run-tests", 0, "all good"), "")
    assert te.framework == "generic"


def test_parse_carries_command_execution_id_and_attribution():
    ce = _ce("c1", "pytest", 0, "1 passed")
    ce.id = 42
    te = parse_test_evidence(ce, "python")
    assert te.command_execution_id == 42
    assert te.command_id == "c1"
    assert te.session_id == "sess-1"
    assert te.workflow_id == "wf-1"
    assert te.milestone_id == "ms-1"
