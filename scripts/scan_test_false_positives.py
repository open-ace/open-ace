#!/usr/bin/env python3
"""
Scan test code for false-positive patterns (Issue #2189, Scope #6).

Detects the four Scope #6 patterns, AST-semantically (not by literal ``pass``,
so a ``print()`` cannot game it — round-2 review):

1. ``broad_except_swallow`` — ``except Exception/BaseException`` whose body has
   no ``raise``/``assert`` and no ``# allow-swallow: <reason>`` marker.
2. ``no_assertion`` — test function with no assertion (``assert``,
   ``pytest.raises``, ``pytest.fail``, unittest/mock assert*).
3. ``return_true`` — test function with a literal ``return True`` and no
   assertion (passes vacuously).
4. ``erroneous_skip`` — ``pytest.skip()`` called unconditionally in a test
   (skips that hide failures).

Kept-but-reviewable instances opt out with explicit markers so the CI gate can
fail on *unannotated* findings without false-noise:
``# allow-swallow``, ``# allow-no-assert``, ``# allow-skip``.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


class Finding(NamedTuple):
    """A false positive finding."""

    file: str
    line: int
    severity: str  # P0, P1, P2
    pattern: str
    message: str
    # Class-qualified function name ("<Class>.<func>", bare "<func>" outside
    # classes, "<module>" at module level) — the stable identity component for
    # the known-debt ledger (line numbers churn, names do not).
    function: str = "<module>"


ASSERT_METHODS = frozenset(
    {
        "assertEqual",
        "assertEquals",
        "assertNotEqual",
        "assertTrue",
        "assertFalse",
        "assertIn",
        "assertNotIn",
        "assertIs",
        "assertIsNot",
        "assertIsNone",
        "assertIsNotNone",
        "assert_called",
        "assert_called_once",
        "assert_called_with",
        "assert_called_once_with",
        "assert_not_called",
        "assertRaises",
        "assertGreater",
        "assertLess",
        "assertGreaterEqual",
        "assertLessEqual",
        "assertAlmostEqual",
        "assertNotAlmostEqual",
        "assertRegex",
        "assertNotRegex",
        "assertCountEqual",
        "assertMultiLineEqual",
        "assertSequenceEqual",
        "assertListEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertDictEqual",
    }
)

# Allow-reason patterns for annotation validation (Issue #2306, REQ-6)
# These patterns define what constitutes a valid annotation reason
ALLOW_REASON_PATTERNS = {
    "allow-no-assert": [
        r"smoke test.*visual verification",
        r"screenshot regression test.*TODO review \d{4}-Q[1-4]",
        r"auto-generated test.*selector alignment",
        r"playwright script.*visual verification",
    ],
    "allow-swallow": [
        r"UI element may not exist",
        r"transient timeout",
        r"screenshot failure.*non-critical",
        r"optional UI element",
        r"test framework error handling",
        r"error screenshot",
        r"best-effort",
        r"idempotent",
        r"cleanup",
        r"collect.*errors",
    ],
    "allow-skip": [
        r"requires external service",
        r"manual-only test",
    ],
}


def validate_annotation_content(annotation: str, pattern_type: str) -> bool:
    """Validate that annotation content matches predefined templates.

    Args:
        annotation: The annotation text (e.g., "smoke test - visual verification only")
        pattern_type: The type of annotation (e.g., "allow-no-assert")

    Returns:
        True if the annotation matches one of the predefined patterns, False otherwise

    Example:
        >>> validate_annotation_content("smoke test - visual verification only", "allow-no-assert")
        True
        >>> validate_annotation_content("just because", "allow-no-assert")
        False
    """
    patterns = ALLOW_REASON_PATTERNS.get(pattern_type, [])
    return any(re.search(p, annotation) for p in patterns)


def _func_start(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """First source line of a function, including decorators."""
    starts = [node.lineno, *(d.lineno for d in node.decorator_list)]
    return min(starts)


def _src_contains(lines: list[str], start_lineno: int, end_lineno: int, needle: str) -> bool:
    """Return True if ``needle`` appears in source lines [start_lineno, end_lineno]."""
    start = max(start_lineno - 1, 0)
    end = min(end_lineno, len(lines))
    return any(needle in lines[i] for i in range(start, end))


def _extract_and_validate_annotation(
    lines: list[str], start_lineno: int, end_lineno: int, pattern_type: str, strict: bool = False
) -> bool:
    """Check if a valid annotation exists in the specified lines.

    Args:
        lines: Source code lines
        start_lineno: Start line number (1-indexed)
        end_lineno: End line number (1-indexed)
        pattern_type: Type of annotation (e.g., "allow-no-assert")
        strict: If True, validate annotation content; if False, just check existence

    Returns:
        True if a valid annotation is found, False otherwise
    """
    start = max(start_lineno - 1, 0)
    end = min(end_lineno, len(lines))

    # Find annotation marker
    marker = f"# {pattern_type}"
    for i in range(start, end):
        if marker in lines[i]:
            if not strict:
                # Non-strict mode: just check existence
                return True

            # Strict mode: extract and validate content
            # Extract the part after the marker
            line = lines[i]
            marker_pos = line.find(marker)
            annotation_content = line[marker_pos + len(marker) :].strip()

            # Remove leading colon if present
            if annotation_content.startswith(":"):
                annotation_content = annotation_content[1:].strip()

            # Validate content
            if validate_annotation_content(annotation_content, pattern_type):
                return True
            else:
                # Invalid annotation content
                import warnings

                warnings.warn(
                    f"Invalid annotation content at line {i + 1}: '{annotation_content}' "
                    f"does not match any template for {pattern_type}",
                    UserWarning,
                    stacklevel=3,
                )
                return False

    return False


def _body_does_not_swallow(body: list[ast.stmt]) -> bool:
    """True if the except body surfaces the failure (does not silently swallow).

    Recognises ``raise``/``assert`` plus calls that themselves raise and thus
    produce a visible test outcome: ``pytest.skip``, ``pytest.fail``,
    ``pytest.raises`` (and ``sys.exit``).
    """
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, (ast.Raise, ast.Assert)):
                return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in {"skip", "fail", "raises", "exit"}:
                    return True
    return False


class FalsePositiveScanner(ast.NodeVisitor):
    """AST visitor to scan for the four Scope #6 false-positive patterns."""

    def __init__(self, filepath: str, source_lines: list[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.findings: list[Finding] = []
        self.current_function: str | None = None
        self.current_func_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.class_stack: list[str] = []
        self.is_test_function = False
        self.has_assertion = False
        self.has_return_true = False
        self.cond_depth = 0  # >0 => inside if/try/with (conditional context)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old = (
            self.current_function,
            self.current_func_node,
            self.is_test_function,
            self.has_assertion,
            self.has_return_true,
            self.cond_depth,
        )
        self.current_function = ".".join([*self.class_stack, node.name])
        self.current_func_node = node
        self.is_test_function = node.name.startswith("test_")
        self.has_assertion = False
        self.has_return_true = False
        self.cond_depth = 0

        self.generic_visit(node)

        if self.is_test_function:
            start = _func_start(node)
            end = node.end_lineno or node.lineno
            # Check for annotation with content validation (Issue #2306)
            # Enable strict validation to enforce annotation content quality
            allow_no_assert = _extract_and_validate_annotation(
                self.source_lines, start, end, "allow-no-assert", strict=True
            )
            if not self.has_assertion and not allow_no_assert:
                self.findings.append(
                    Finding(
                        file=self.filepath,
                        line=node.lineno,
                        severity="P0",
                        pattern="no_assertion",
                        message=f"Test function '{node.name}' has no assertions",
                        function=self.current_function or "<module>",
                    )
                )
            if self.has_return_true and not self.has_assertion and not allow_no_assert:
                self.findings.append(
                    Finding(
                        file=self.filepath,
                        line=node.lineno,
                        severity="P0",
                        pattern="return_true",
                        message=f"Test function '{node.name}' returns True without asserting",
                        function=self.current_function or "<module>",
                    )
                )

        (
            self.current_function,
            self.current_func_node,
            self.is_test_function,
            self.has_assertion,
            self.has_return_true,
            self.cond_depth,
        ) = old

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        broad = (
            node.type
            and isinstance(node.type, ast.Name)
            and node.type.id in ("Exception", "BaseException")
        )
        if broad:
            end = node.end_lineno or node.lineno
            # Check for annotation with content validation (Issue #2306)
            # Enable strict validation to enforce annotation content quality
            allow = _extract_and_validate_annotation(
                self.source_lines, node.lineno, end, "allow-swallow", strict=True
            )
            if not _body_does_not_swallow(node.body) and not allow:
                severity = "P0" if self.is_test_function else "P1"
                self.findings.append(
                    Finding(
                        file=self.filepath,
                        line=node.lineno,
                        severity=severity,
                        pattern="broad_except_swallow",
                        message=(
                            f"Broad except {node.type.id} swallows (no raise/assert) "
                            f"in '{self.current_function}'"
                        ),
                        function=self.current_function or "<module>",
                    )
                )
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.has_assertion = True
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            self.has_return_true = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ASSERT_METHODS:
                self.has_assertion = True
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest":
                if node.func.attr in {"raises", "fail"}:
                    self.has_assertion = True
                if node.func.attr == "skip" and self.is_test_function and self.cond_depth == 0:
                    fn = self.current_func_node
                    allow_skip = fn is not None and _src_contains(
                        self.source_lines,
                        _func_start(fn),
                        fn.end_lineno or fn.lineno,
                        "allow-skip",
                    )
                    if not allow_skip:
                        self.findings.append(
                            Finding(
                                file=self.filepath,
                                line=node.lineno,
                                severity="P1",
                                pattern="erroneous_skip",
                                message=(
                                    f"Unconditional pytest.skip() in '{self.current_function}' "
                                    f"hides failures"
                                ),
                                function=self.current_function or "<module>",
                            )
                        )
        self.generic_visit(node)

    def _enter_conditional(self, node: ast.AST) -> None:
        self.cond_depth += 1
        self.generic_visit(node)
        self.cond_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self._enter_conditional(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._enter_conditional(node)

    def visit_With(self, node: ast.With) -> None:
        self._enter_conditional(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._enter_conditional(node)


def scan_file(filepath: Path) -> list[Finding]:
    """Scan a single file for false positive patterns."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    if not ("def test_" in content or "def e2e_" in content):
        return []

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return []

    visitor = FalsePositiveScanner(str(filepath), content.splitlines())
    visitor.visit(tree)
    return visitor.findings


def _is_excluded(rel_path: Path, exclude_dirs: list[str], root_name: str = "") -> bool:
    """Return True if ``rel_path`` falls under one of the exclude dirs.

    Each exclude may be specified either relative to the scan root (``issues``)
    or with the scan-root prefix (``tests/issues``); both are accepted.
    """
    posix = rel_path.as_posix()
    prefix = f"{root_name}/" if root_name else ""
    for excl in exclude_dirs:
        norm = excl.strip().strip("/").rstrip("/")
        if not norm:
            continue
        if prefix and norm.startswith(prefix):
            norm = norm[len(prefix) :]
        if posix == norm or posix.startswith(norm + "/"):
            return True
    return False


def scan_tests(
    test_dir: Path,
    pattern: str = "all",
    exclude_dirs: list[str] | None = None,
) -> list[Finding]:
    """Scan all test files for false positive patterns."""
    exclude_dirs = exclude_dirs or []
    all_findings: list[Finding] = []

    for py_file in test_dir.rglob("*.py"):
        if not (py_file.name.startswith(("test_", "e2e_")) or py_file.name.endswith("_test.py")):
            continue
        try:
            rel = py_file.relative_to(test_dir)
        except ValueError:
            rel = py_file
        if _is_excluded(rel, exclude_dirs, root_name=test_dir.name):
            continue

        findings = scan_file(py_file)
        if pattern != "all":
            findings = [f for f in findings if f.pattern == pattern]
        all_findings.extend(findings)

    return all_findings


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Exit code is 1 if any P0 finding remains after filtering."""
    project_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description="Scan test code for false-positive patterns (Issue #2189, Scope #6)."
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default="tests",
        help="Root test directory to scan (default: tests). Relative paths "
        "resolve against the project root.",
    )
    parser.add_argument(
        "--pattern",
        choices=["all", "broad_except_swallow", "no_assertion", "return_true", "erroneous_skip"],
        default="all",
        help="Restrict the scan to a single pattern (default: all).",
    )
    parser.add_argument(
        "--exclude-dirs",
        nargs="*",
        default=[],
        metavar="DIR",
        help="Directory subtrees to skip (e.g. tests/issues).",
    )
    parser.add_argument(
        "--baseline",
        metavar="FILE",
        help="Baseline JSON {pattern: count}. Exit 1 if any pattern's count "
        "EXCEEDS the baseline (regression gate that ratchets down as findings "
        "are fixed).",
    )
    parser.add_argument(
        "--update-baseline",
        metavar="FILE",
        help="Write the current per-pattern counts to FILE and exit 0.",
    )
    parser.add_argument(
        "--ledger",
        metavar="FILE",
        help="Known-debt ledger JSON (precise per-finding identities). Exit 1 on "
        "any finding whose identity is absent from the ledger (or exceeds its "
        "recorded count) AND on any stale ledger entry. Relative paths resolve "
        "against the project root.",
    )
    parser.add_argument(
        "--prune-ledger",
        metavar="FILE",
        help="Removal-only ledger maintenance: drop stale entries and lower "
        "counts to the current values, then exit 0. REFUSES (exit 1) if any "
        "current finding's identity is absent from the ledger or would need a "
        "count increase — pruning can never enroll new debt.",
    )
    args = parser.parse_args(argv)

    test_dir = Path(args.dir)
    if not test_dir.is_absolute():
        test_dir = project_root / test_dir

    print(f"Scanning {test_dir} for false positive patterns (Issue #2189, Scope #6)...")
    print(f"  pattern={args.pattern!r} exclude_dirs={args.exclude_dirs}")
    print("=" * 70)

    findings = scan_tests(test_dir, pattern=args.pattern, exclude_dirs=args.exclude_dirs)
    findings.sort(key=lambda f: (f.severity, f.file, f.line))

    by_pattern: dict[str, int] = {}
    for finding in findings:
        by_pattern[finding.pattern] = by_pattern.get(finding.pattern, 0) + 1

    p0_count = sum(1 for f in findings if f.severity == "P0")
    p1_count = sum(1 for f in findings if f.severity == "P1")
    p2_count = sum(1 for f in findings if f.severity == "P2")

    print("\nFindings by severity:")
    print(f"  P0 (must fix): {p0_count}")
    print(f"  P1 (review needed): {p1_count}")
    print(f"  P2 (low priority): {p2_count}")
    print("Findings by pattern:")
    for patt, cnt in sorted(by_pattern.items()):
        print(f"  {patt}: {cnt}")
    print("=" * 70)

    if args.update_baseline:
        Path(args.update_baseline).write_text(
            json.dumps(by_pattern, indent=2, sort_keys=True) + "\n"
        )
        print(f"Wrote baseline to {args.update_baseline}")
        return 0

    if args.baseline:
        return _compare_baseline(Path(args.baseline), by_pattern)

    if args.ledger or args.prune_ledger:
        if args.ledger and args.prune_ledger:
            print("::error::--ledger and --prune-ledger are mutually exclusive")
            return 1
        ledger_path = Path(args.ledger or args.prune_ledger)
        if not ledger_path.is_absolute():
            ledger_path = project_root / ledger_path
        if args.prune_ledger:
            return _prune_ledger(ledger_path, findings, project_root)
        return _compare_ledger(ledger_path, findings, project_root)

    for finding in findings:
        print(f"\n[{finding.severity}] {finding.pattern}")
        print(f"  File: {finding.file}:{finding.line}")
        print(f"  Message: {finding.message}")

    print("\n" + "=" * 70)
    print(f"Total findings: {len(findings)}")

    return 1 if p0_count > 0 else 0


def _compare_baseline(baseline_path: Path, by_pattern: dict[str, int]) -> int:
    """Exit 1 if any pattern's count exceeds the recorded baseline."""
    try:
        baseline = json.loads(baseline_path.read_text())
    except OSError as e:
        print(f"::error::Cannot read baseline {baseline_path}: {e}")
        return 1
    regressions = []
    for patt, current in sorted(by_pattern.items()):
        allowed = baseline.get(patt, 0)
        if current > allowed:
            regressions.append((patt, current, allowed))
        print(f"  {patt}: {current} (baseline {allowed})")
    for patt, current, allowed in regressions:
        print(
            f"::error::{patt} count {current} exceeds baseline {allowed} "
            f"(regression — fix the new finding or run --update-baseline)"
        )
    return 1 if regressions else 0


# ---------------------------------------------------------------------------
# Known-debt ledger (Issue #3186 Phase A): precise per-finding identities so
# the CI gate can distinguish "new debt" from "known debt" and make
# same-count substitution (fix A, introduce B) programmatically red. The
# count-based --baseline above is retired from CI consumption; it remains for
# backwards compatibility only.
# ---------------------------------------------------------------------------

LEDGER_VERSION = 1
MODULE_LEVEL = "<module>"


def _ledger_identity(finding: Finding, root: Path) -> tuple[str, str, str]:
    """Stable identity: (pattern, repo-relative file, class-qualified function)."""
    try:
        rel = Path(finding.file).relative_to(root)
    except ValueError:
        rel = Path(finding.file)
    return (finding.pattern, rel.as_posix(), finding.function or MODULE_LEVEL)


def _current_counts(findings: list[Finding], root: Path) -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    for finding in findings:
        identity = _ledger_identity(finding, root)
        counts[identity] = counts.get(identity, 0) + 1
    return counts


def _load_ledger(path: Path) -> dict[tuple[str, str, str], int]:
    """Load and validate the ledger file; malformed input raises LedgerError."""
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        raise LedgerError(f"Cannot read ledger {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise LedgerError(f"Ledger {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LedgerError(f"Ledger {path} must be a JSON object, got {type(data).__name__}")
    if data.get("version") != LEDGER_VERSION:
        raise LedgerError(f"Ledger {path} has unsupported version {data.get('version')!r}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise LedgerError(f"Ledger {path} must contain an 'entries' list")
    counts: dict[tuple[str, str, str], int] = {}
    for entry in entries:
        try:
            identity = (entry["pattern"], entry["file"], entry.get("function") or MODULE_LEVEL)
            count = int(entry.get("count", 1))
        except (KeyError, TypeError, ValueError) as e:
            raise LedgerError(f"Ledger {path} has a malformed entry {entry!r}: {e}") from e
        if identity in counts:
            raise LedgerError(f"Ledger {path} has duplicate entry for {identity}")
        if count < 1:
            raise LedgerError(f"Ledger {path} entry {identity} has non-positive count {count}")
        counts[identity] = count
    return counts


class LedgerError(RuntimeError):
    """Invalid ledger file (unreadable, wrong version, malformed entries)."""


def _render_identity(identity: tuple[str, str, str]) -> str:
    return f"{identity[0]} | {identity[1]} | {identity[2]}"


def _compare_ledger(ledger_path: Path, findings: list[Finding], root: Path) -> int:
    """Exit 1 on unknown findings (new debt) or stale ledger entries.

    Comparison is count-exact per identity: a second hit under an existing
    identity must be covered by the recorded count, and a count above the
    current value marks the entry stale (the debt was fixed — prune it).
    """
    try:
        ledger = _load_ledger(ledger_path)
    except LedgerError as e:
        print(f"::error::{e}")
        return 1
    current = _current_counts(findings, root)

    problems = []
    for identity, count in sorted(current.items()):
        allowed = ledger.get(identity, 0)
        if count > allowed:
            problems.append(
                f"unknown finding {identity[2]} in {identity[1]} "
                f"({identity[0]}): {count} hit(s), ledger covers {allowed}"
            )
    for identity, allowed in sorted(ledger.items()):
        count = current.get(identity, 0)
        if count < allowed:
            problems.append(
                f"stale ledger entry {identity[2]} in {identity[1]} "
                f"({identity[0]}): ledger covers {allowed}, current {count} "
                f"— fix confirmed, prune with --prune-ledger"
            )
    for problem in problems:
        print(f"::error::{problem}")
    print(f"Ledger check: {len(current)} finding identities, {len(ledger)} ledger entries")
    if problems:
        print(f"FAILED: {len(problems)} problem(s) — new debt is never enrollable via prune")
        return 1
    print("OK: every finding is known debt; ledger mirrors reality exactly")
    return 0


def _prune_ledger(ledger_path: Path, findings: list[Finding], root: Path) -> int:
    """Removal-only ledger maintenance; refuses to enroll anything.

    Drops identities the scan no longer finds and lowers counts to the
    current values. If any current identity is absent from the ledger, or a
    count would have to rise, refuse (exit 1): pruning can never absorb new
    debt — enrollment is only possible by hand-editing the ledger, which the
    FROZEN_SEED contract test (tests/unit/test_ci_runner.py) then rejects.
    """
    try:
        ledger = _load_ledger(ledger_path)
    except LedgerError as e:
        print(f"::error::{e}")
        return 1
    current = _current_counts(findings, root)

    refusals = []
    for identity, count in sorted(current.items()):
        allowed = ledger.get(identity, 0)
        if allowed == 0:
            refusals.append(
                f"unknown finding {identity[2]} in {identity[1]} "
                f"({identity[0]}) — prune cannot enroll new debt; fix the "
                f"finding or hand-edit the ledger (contract test guards this)"
            )
        elif count > allowed:
            refusals.append(
                f"count increase for {identity[2]} in {identity[1]} "
                f"({identity[0]}): {allowed} -> {count} — prune cannot raise counts"
            )
    for refusal in refusals:
        print(f"::error::{refusal}")
    if refusals:
        print(f"FAILED: prune refused ({len(refusals)} problem(s)); ledger left unchanged")
        return 1

    removed = [
        (identity, allowed) for identity, allowed in ledger.items() if identity not in current
    ]
    reduced = [
        (identity, allowed, current[identity])
        for identity, allowed in ledger.items()
        if identity in current and current[identity] < allowed
    ]
    if not removed and not reduced:
        print(f"Prune: no changes (ledger already mirrors reality: {len(current)} entries)")
        return 0
    entries = [
        {"pattern": identity[0], "file": identity[1], "function": identity[2], "count": count}
        for identity, count in sorted(current.items())
    ]
    ledger_path.write_text(
        json.dumps({"version": LEDGER_VERSION, "entries": entries}, indent=2) + "\n"
    )
    print(f"Pruned ledger written to {ledger_path}: {len(entries)} entries")
    for identity, allowed in sorted(removed):
        print(f"  removed (fixed): {_render_identity(identity)} (was {allowed})")
    for identity, allowed, now in sorted(reduced):
        print(f"  reduced: {_render_identity(identity)} {allowed} -> {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
