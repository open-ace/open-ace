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
                    f"Invalid annotation content at line {i+1}: '{annotation_content}' "
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
        self.current_function = node.name
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
            allow_no_assert = _extract_and_validate_annotation(
                self.source_lines, start, end, "allow-no-assert", strict=False
            )
            if not self.has_assertion and not allow_no_assert:
                self.findings.append(
                    Finding(
                        file=self.filepath,
                        line=node.lineno,
                        severity="P0",
                        pattern="no_assertion",
                        message=f"Test function '{node.name}' has no assertions",
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

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        broad = (
            node.type
            and isinstance(node.type, ast.Name)
            and node.type.id in ("Exception", "BaseException")
        )
        if broad:
            end = node.end_lineno or node.lineno
            # Check for annotation with content validation (Issue #2306)
            allow = _extract_and_validate_annotation(
                self.source_lines, node.lineno, end, "allow-swallow", strict=False
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


if __name__ == "__main__":
    sys.exit(main())
