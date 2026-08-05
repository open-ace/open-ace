"""Structured test-execution evidence (#2046 Phase B).

Domain module under ``app/`` (not a unit test — pytest collects only ``tests/``);
the ``test_`` prefix means *test-execution* evidence, not a test module.

Where :mod:`types` captures *command* execution facts (what ran, how it
terminated), this module captures *test* execution facts derived from a
command's output — framework, collected/passed/failed counts, selectors,
and an authoritative verdict. A pluggable parser per framework reads the
``CommandExecutionEvidence.output_excerpt`` + ``exit_code``; it never reads
the agent's free-form summary text as an authoritative source (#1967, #2046).

Phase A persists ``CommandExecutionEvidence`` and shadow-compares a
command-level verdict against the legacy heuristic. Phase B adds this
test-level evidence: the gate's authoritative PASSED/FAILED comes from
``compute_run_verdict`` over a list of ``TestExecutionEvidence``. The legacy
heuristic stays only as an INCONCLUSIVE/NOT_RUN fallback (#2046 §4 降级).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.modules.workspace.autonomous.command_evidence.scope import _pytest_test_scope, _PytestScope
from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)


class ParserConfidence(str, Enum):
    """How strongly a parser trusts its own parsed verdict.

    ``HIGH`` — matched the framework's authoritative summary line (pytest
    "N passed in N.Ns", jest "Tests: N passed", go "ok pkg"). ``MEDIUM`` — saw
    a pass/fail marker or a clean exit code but no parseable count.
    ``LOW`` — generic, fell back to exit code only with no framework signal.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ParsedTestResult:
    """Framework-level parse of one command's test output (in-memory)."""

    framework: str
    collected: int | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    errors: int | None = None
    selectors: list[str] = field(default_factory=list)
    coverage_scope: dict[str, Any] | None = None
    parser: str = ""
    parser_confidence: str = ParserConfidence.LOW.value
    verdict: str = ExecutionVerdict.INCONCLUSIVE.value


@dataclass
class TestExecutionEvidence:
    """Authoritative test-execution facts for one command (#2046 Phase B).

    Pairs 1:1 with a ``CommandExecutionEvidence`` row via
    ``command_execution_id`` (the row PK) and shares its ``(session_id,
    command_id)`` identity. ``verdict`` is a single command's
    :class:`ExecutionVerdict` value; the run-level verdict is computed by
    :func:`app.modules.workspace.autonomous.command_evidence.test_verdict.compute_run_verdict`.
    """

    command_id: str
    command_execution_id: int | None = None
    framework: str = ""
    collected: int | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    errors: int | None = None
    selectors: list[str] = field(default_factory=list)
    coverage_scope: dict[str, Any] | None = None
    parser: str = ""
    parser_confidence: str = ""
    verdict: str = ""
    # Persistence / attribution (mirror CommandExecutionEvidence).
    session_id: str = ""
    workflow_id: str = ""
    milestone_id: str = ""
    tenant_id: int = 1
    id: int | None = None
    created_at: datetime | None = None

    # pytest collects classes named ``Test*``; this is a data class, not a test.
    __test__ = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for storage/API/metadata."""
        data = asdict(self)
        value = data.get("created_at")
        data["created_at"] = value.isoformat() if isinstance(value, datetime) else value
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TestExecutionEvidence:
        """Build an instance from a DB row dict (JSON fields already decoded)."""
        selectors = row.get("selectors")
        if isinstance(selectors, str):
            import json

            try:
                selectors = json.loads(selectors) if selectors else []
            except (TypeError, ValueError):
                selectors = []
        coverage_scope = row.get("coverage_scope")
        if isinstance(coverage_scope, str):
            import json

            try:
                coverage_scope = json.loads(coverage_scope) if coverage_scope else None
            except (TypeError, ValueError):
                coverage_scope = None
        return cls(
            id=row.get("id"),
            command_id=row.get("command_id") or "",
            command_execution_id=row.get("command_execution_id"),
            framework=row.get("framework") or "",
            collected=row.get("collected"),
            passed=row.get("passed"),
            failed=row.get("failed"),
            skipped=row.get("skipped"),
            errors=row.get("errors"),
            selectors=selectors or [],
            coverage_scope=coverage_scope,
            parser=row.get("parser") or "",
            parser_confidence=row.get("parser_confidence") or "",
            verdict=row.get("verdict") or "",
            session_id=row.get("session_id") or "",
            workflow_id=row.get("workflow_id") or "",
            milestone_id=row.get("milestone_id") or "",
            tenant_id=int(row.get("tenant_id") or 1),
            created_at=_parse_dt(row.get("created_at")),
        )


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# ── Count extraction (multilingual) ─────────────────────────────────────────
# Each list is tried in order; all matches across the excerpt are summed so a
# summary repeated in header + footer is not double-counted past its real
# total (we take the max per-kind, not the sum, to stay conservative — see
# _extract_count).
_PASSED_PATTERNS = [
    re.compile(r"\b(\d+)\s+passed\b", re.IGNORECASE),
    re.compile(r"test result:\s*ok\.\s*(\d+)\s+passed", re.IGNORECASE),
    re.compile(r"(?:通过|成功)[:：\s]+(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:个|项|件)?\s*(?:测试)?\s*(?:全部|全都|都)?\s*(?:通过|成功)"),
    re.compile(r"(?:通過|成功)[:：\s]+(\d+)\s*(?:件|テスト)?"),
    re.compile(r"(?:통과|성공)[:：\s]+(\d+)\s*(?:개|테스트)?"),
]
_FAILED_PATTERNS = [
    re.compile(r"\b(\d+)\s+failed\b", re.IGNORECASE),
    re.compile(r"test result:\s*FAILED\.\s*(\d+)\s+failed", re.IGNORECASE),
    re.compile(r"(?:失败|错误)[:：\s]+(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:个|项|件)?\s*(?:测试)?\s*(?:失败|错误)"),
    re.compile(r"(?:失敗|エラー)[:：\s]+(\d+)\s*(?:件|テスト)?"),
    re.compile(r"(?:실패|오류)[:：\s]+(\d+)\s*(?:개|테스트)?"),
]
_SKIPPED_PATTERNS = [
    re.compile(r"\b(\d+)\s+skipped\b", re.IGNORECASE),
    re.compile(r"(?:跳过|忽略)[:：\s]+(\d+)", re.IGNORECASE),
    re.compile(r"(?:スキップ|スキップ済み)[:：\s]+(\d+)\s*(?:件|テスト)?"),
    re.compile(r"(?:건너뜀|스킵)[:：\s]+(\d+)\s*(?:개|테스트)?"),
]
_ERRORS_PATTERNS = [
    re.compile(r"\b(\d+)\s+errors?\b", re.IGNORECASE),
    re.compile(r"errors?[:：\s]+(\d+)", re.IGNORECASE),
]


def _extract_count(excerpt: str, patterns: list[re.Pattern[str]]) -> int | None:
    """Return the strongest count any pattern matched, or None.

    Takes the max (not the sum) so a summary echoed in both header and footer
    does not inflate the count past its real value.
    """
    best: int | None = None
    for pattern in patterns:
        for match in pattern.finditer(excerpt):
            try:
                value = int(match.group(1))
            except (ValueError, IndexError):
                continue
            if best is None or value > best:
                best = value
    return best


def _scope_to_dict(scope: _PytestScope | None) -> dict[str, Any] | None:
    """Serialize a pytest scope to a JSON-friendly dict for persistence."""
    if scope is None:
        return None
    context, selectors = scope
    return {"context": context, "selectors": sorted(selectors)}


def _scope_from_dict(data: dict[str, Any] | None) -> _PytestScope | None:
    """Reconstruct a pytest scope from its persisted dict form."""
    if not data:
        return None
    context = data.get("context")
    selectors = data.get("selectors") or []
    if not isinstance(context, str):
        return None
    return context, frozenset(str(s) for s in selectors)


def _verdict_from_counts(
    *, passed: int | None, failed: int | None, errors: int | None
) -> ExecutionVerdict:
    """Map parsed counts to a verdict. Called only when at least one matched."""
    if (failed or 0) > 0 or (errors or 0) > 0:
        return ExecutionVerdict.FAILED
    if (passed or 0) > 0:
        return ExecutionVerdict.PASSED
    return ExecutionVerdict.INCONCLUSIVE


def _parse_pytest(excerpt: str, exit_code: int | None, command_text: str) -> ParsedTestResult:
    """Parse pytest (and unittest) output into structured counts + scope."""
    passed = _extract_count(excerpt, _PASSED_PATTERNS)
    failed = _extract_count(excerpt, _FAILED_PATTERNS)
    skipped = _extract_count(excerpt, _SKIPPED_PATTERNS)
    errors = _extract_count(excerpt, _ERRORS_PATTERNS)

    # unittest "OK (N tests)" / "FAILED (failures=N)" have no "passed" token;
    # synthesize a pass count so a clean unittest run is not stuck inconclusive.
    unittest_ok = re.search(r"\bRan\s+(\d+)\s+tests?\b[\s\S]*?^OK\s*$", excerpt, re.MULTILINE)
    if unittest_ok and passed is None and failed is None:
        passed = int(unittest_ok.group(1))

    scope = _pytest_test_scope(command_text) if command_text else None
    selectors = sorted(scope[1]) if scope else []
    coverage_scope = _scope_to_dict(scope)

    saw_summary = passed is not None or failed is not None or errors is not None
    if saw_summary:
        verdict = _verdict_from_counts(passed=passed, failed=failed, errors=errors)
        confidence = ParserConfidence.HIGH
    elif exit_code is not None:
        # Saw a clean/non-clean exit but no parseable pytest summary. Trust
        # the exit code at medium confidence (the process did finish).
        verdict = ExecutionVerdict.PASSED if exit_code == 0 else ExecutionVerdict.FAILED
        confidence = ParserConfidence.MEDIUM
    else:
        verdict = ExecutionVerdict.INCONCLUSIVE
        confidence = ParserConfidence.LOW

    return ParsedTestResult(
        framework="python",
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        selectors=selectors,
        coverage_scope=coverage_scope,
        parser="pytest",
        parser_confidence=confidence.value,
        verdict=verdict.value,
    )


def _parse_jest(excerpt: str, exit_code: int | None) -> ParsedTestResult:
    """Parse Jest output ("Tests: N passed, N failed")."""
    passed = _extract_count(
        excerpt,
        [
            re.compile(r"Tests:\s*(\d+)\s+passed", re.IGNORECASE),
            re.compile(r"(\d+)\s+tests?\s+passed", re.IGNORECASE),
        ],
    )
    failed = _extract_count(
        excerpt,
        [
            re.compile(r"Tests:\s*(\d+)\s+failed", re.IGNORECASE),
            re.compile(r"(\d+)\s+tests?\s+failed", re.IGNORECASE),
        ],
    )
    if passed is not None or failed is not None:
        verdict = _verdict_from_counts(passed=passed, failed=failed, errors=None)
        confidence = ParserConfidence.HIGH
    elif exit_code is not None:
        verdict = ExecutionVerdict.PASSED if exit_code == 0 else ExecutionVerdict.FAILED
        confidence = ParserConfidence.MEDIUM
    else:
        verdict = ExecutionVerdict.INCONCLUSIVE
        confidence = ParserConfidence.LOW
    return ParsedTestResult(
        framework="javascript",
        passed=passed,
        failed=failed,
        parser="jest",
        parser_confidence=confidence.value,
        verdict=verdict.value,
    )


def _parse_go(excerpt: str, exit_code: int | None) -> ParsedTestResult:
    """Parse `go test` output ("ok pkg N.Ns" / "FAIL pkg N.Ns")."""
    ok = re.search(r"^ok\s+\S+\s+[\d.]+s", excerpt, re.MULTILINE)
    fail = re.search(r"^FAIL\s+\S+", excerpt, re.MULTILINE)
    if fail:
        verdict, confidence = ExecutionVerdict.FAILED, ParserConfidence.HIGH
    elif ok:
        verdict, confidence = ExecutionVerdict.PASSED, ParserConfidence.HIGH
    elif exit_code is not None:
        verdict = ExecutionVerdict.PASSED if exit_code == 0 else ExecutionVerdict.FAILED
        confidence = ParserConfidence.MEDIUM
    else:
        verdict, confidence = ExecutionVerdict.INCONCLUSIVE, ParserConfidence.LOW
    return ParsedTestResult(
        framework="go",
        parser="go_test",
        parser_confidence=confidence.value,
        verdict=verdict.value,
    )


def _parse_cargo(excerpt: str, exit_code: int | None) -> ParsedTestResult:
    """Parse `cargo test` output ("test result: ok. N passed; N failed")."""
    passed = _extract_count(
        excerpt, [re.compile(r"test result:\s*ok\.\s*(\d+)\s+passed", re.IGNORECASE)]
    )
    failed = _extract_count(
        excerpt,
        [re.compile(r"(\d+)\s+failed", re.IGNORECASE)],
    )
    saw_summary = "test result:" in excerpt.lower()
    if saw_summary and (passed is not None or failed is not None):
        verdict = _verdict_from_counts(passed=passed, failed=failed, errors=None)
        confidence = ParserConfidence.HIGH
    elif "test result: ok" in excerpt.lower():
        verdict, confidence = ExecutionVerdict.PASSED, ParserConfidence.HIGH
    elif exit_code is not None:
        verdict = ExecutionVerdict.PASSED if exit_code == 0 else ExecutionVerdict.FAILED
        confidence = ParserConfidence.MEDIUM
    else:
        verdict, confidence = ExecutionVerdict.INCONCLUSIVE, ParserConfidence.LOW
    return ParsedTestResult(
        framework="rust",
        passed=passed,
        failed=failed,
        parser="cargo",
        parser_confidence=confidence.value,
        verdict=verdict.value,
    )


def _parse_generic(excerpt: str, exit_code: int | None) -> ParsedTestResult:
    """Fallback parser: only the process exit code is authoritative.

    Used when the framework is unknown or unrecognized. A zero exit with some
    output is MEDIUM confidence (the process claimed success); no exit code at
    all is INCONCLUSIVE — the gate then falls back to the heuristic.
    """
    if exit_code is None:
        verdict, confidence = ExecutionVerdict.INCONCLUSIVE, ParserConfidence.LOW
    elif exit_code == 0:
        verdict, confidence = (
            ExecutionVerdict.PASSED if excerpt.strip() else ExecutionVerdict.INCONCLUSIVE,
            ParserConfidence.MEDIUM if excerpt.strip() else ParserConfidence.LOW,
        )
    else:
        verdict, confidence = ExecutionVerdict.FAILED, ParserConfidence.MEDIUM
    return ParsedTestResult(
        framework="generic",
        parser="generic",
        parser_confidence=confidence.value,
        verdict=verdict.value,
    )


def _resolve_framework(command_evidence: CommandExecutionEvidence, hint: str) -> str:
    """Pick the parser framework from the command text, falling back to hint.

    The command's shell text is authoritative for which framework ran; the
    project-level hint is the fallback when the command gives no signal.
    ``go`` / ``cargo`` are matched on the leading token so ``cargo test`` is
    not mistaken for ``go test`` (``car`` + ``go test`` is a substring match).
    """
    command_text = (command_evidence.shell_command or "").lower()
    tokens = command_text.split()
    head = tokens[0] if tokens else ""
    if any(signal in command_text for signal in ("pytest", "py.test", "unittest")):
        return "python"
    if any(signal in command_text for signal in ("jest", "vitest", "npm test", "yarn test")):
        return "javascript"
    if head == "go":
        return "go"
    if head == "cargo":
        return "rust"
    return hint or "generic"


def parse_test_evidence(
    command_evidence: CommandExecutionEvidence, framework_hint: str = ""
) -> TestExecutionEvidence:
    """Parse one command's evidence into structured :class:`TestExecutionEvidence`.

    Dispatches on the resolved framework. ``framework_hint`` is the project-
    level inference (``_infer_test_framework``); the command's own shell text
    overrides it when it carries a stronger signal.
    """
    framework = _resolve_framework(command_evidence, framework_hint)
    excerpt = command_evidence.output_excerpt or ""
    exit_code = command_evidence.exit_code
    command_text = command_evidence.shell_command or ""

    if framework == "python":
        parsed = _parse_pytest(excerpt, exit_code, command_text)
    elif framework == "javascript":
        parsed = _parse_jest(excerpt, exit_code)
    elif framework == "go":
        parsed = _parse_go(excerpt, exit_code)
    elif framework == "rust":
        parsed = _parse_cargo(excerpt, exit_code)
    else:
        parsed = _parse_generic(excerpt, exit_code)

    return TestExecutionEvidence(
        command_id=command_evidence.command_id,
        command_execution_id=command_evidence.id,
        framework=parsed.framework,
        collected=parsed.collected,
        passed=parsed.passed,
        failed=parsed.failed,
        skipped=parsed.skipped,
        errors=parsed.errors,
        selectors=parsed.selectors,
        coverage_scope=parsed.coverage_scope,
        parser=parsed.parser,
        parser_confidence=parsed.parser_confidence,
        verdict=parsed.verdict,
        session_id=command_evidence.session_id,
        workflow_id=command_evidence.workflow_id,
        milestone_id=command_evidence.milestone_id,
        tenant_id=command_evidence.tenant_id,
    )
