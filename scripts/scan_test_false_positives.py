#!/usr/bin/env python3
"""
Scan test code for false positive patterns (Issue #2189).

Detects:
1. Broad except: pass in test functions (P0)
2. Test functions without assertions (P0)
3. Broad except: pass in helper functions (P1)
"""

import argparse
import ast
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


class FalsePositiveScanner(ast.NodeVisitor):
    """AST visitor to scan for false positive patterns."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.findings: list[Finding] = []
        self.current_function: str | None = None
        self.is_test_function = False
        self.has_assertion = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition."""
        # Track current function
        old_function = self.current_function
        old_is_test = self.is_test_function
        old_has_assertion = self.has_assertion

        self.current_function = node.name
        self.is_test_function = node.name.startswith("test_")
        self.has_assertion = False

        # Visit function body
        self.generic_visit(node)

        # Check for test functions without assertions
        if self.is_test_function and not self.has_assertion:
            self.findings.append(
                Finding(
                    file=self.filepath,
                    line=node.lineno,
                    severity="P0",
                    pattern="no_assertion",
                    message=f"Test function '{node.name}' has no assertions",
                )
            )

        # Restore state
        self.current_function = old_function
        self.is_test_function = old_is_test
        self.has_assertion = old_has_assertion

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Visit except handler to detect broad except: pass."""
        # Check for broad except: pass
        if node.type and isinstance(node.type, ast.Name):
            if node.type.id in ("Exception", "BaseException"):
                for child in node.body:
                    if isinstance(child, ast.Pass):
                        severity = "P0" if self.is_test_function else "P1"
                        self.findings.append(
                            Finding(
                                file=self.filepath,
                                line=node.lineno,
                                severity=severity,
                                pattern="broad_except_pass",
                                message=f"Broad except {node.type.id}: pass in function '{self.current_function}'",
                            )
                        )

        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        """Visit assert statement."""
        self.has_assertion = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call to detect assertions."""
        # Check for assert statements or pytest.raises
        if isinstance(node.func, ast.Name):
            if node.func.id == "assert":
                self.has_assertion = True
        elif isinstance(node.func, ast.Attribute):
            # Check for standard assertions
            if node.func.attr in (
                "assert",
                "assertEqual",
                "assertTrue",
                "assertFalse",
                "assertIn",
                "assertIs",
                "assertIsNot",
                "assert_called",
                "assert_called_once",
                "assert_called_with",
                "assert_not_called",
                "assert_called_once_with",
            ):
                self.has_assertion = True
            # Check for pytest.raises
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.func.attr == "raises"
            ):
                self.has_assertion = True

        self.generic_visit(node)


def has_assertion_regex(content: str) -> bool:
    """Check if file has assertions using regex."""
    # Check for assert statements or pytest.raises
    return bool(re.search(r"\bassert\b|pytest\.raises", content))


def scan_file(filepath: Path) -> list[Finding]:
    """Scan a single file for false positive patterns."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    # Skip files without test code
    if not ("def test_" in content or "def e2e_" in content):
        return []

    # Parse AST
    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return []

    # Visit AST
    visitor = FalsePositiveScanner(str(filepath))
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
    """Scan all test files for false positive patterns.

    Args:
        test_dir: Root directory to scan recursively.
        pattern: Restrict to a single pattern (``broad_except_pass``,
            ``no_assertion``, or ``all``).
        exclude_dirs: Directory subtrees (relative to ``test_dir``) to skip,
            e.g. auto-generated ``tests/issues``.
    """
    exclude_dirs = exclude_dirs or []
    all_findings: list[Finding] = []

    for py_file in test_dir.rglob("*.py"):
        # Only scan test files
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
    """Main entry point.

    Exit code is 1 if any P0 finding remains after pattern/exclude filtering.
    """
    project_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description="Scan test code for false-positive patterns (Issue #2189)."
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
        choices=["all", "broad_except_pass", "no_assertion"],
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
    args = parser.parse_args(argv)

    test_dir = Path(args.dir)
    if not test_dir.is_absolute():
        test_dir = project_root / test_dir

    print(f"Scanning {test_dir} for false positive patterns (Issue #2189)...")
    print(f"  pattern={args.pattern!r} exclude_dirs={args.exclude_dirs}")
    print("=" * 70)

    findings = scan_tests(test_dir, pattern=args.pattern, exclude_dirs=args.exclude_dirs)

    if not findings:
        print("No false positive patterns detected.")
        return 0

    # Sort by severity
    findings.sort(key=lambda f: (f.severity, f.file, f.line))

    # Print findings
    p0_count = sum(1 for f in findings if f.severity == "P0")
    p1_count = sum(1 for f in findings if f.severity == "P1")
    p2_count = sum(1 for f in findings if f.severity == "P2")

    print("\nFindings by severity:")
    print(f"  P0 (must fix): {p0_count}")
    print(f"  P1 (review needed): {p1_count}")
    print(f"  P2 (low priority): {p2_count}")
    print("=" * 70)

    for finding in findings:
        print(f"\n[{finding.severity}] {finding.pattern}")
        print(f"  File: {finding.file}:{finding.line}")
        print(f"  Message: {finding.message}")

    print("\n" + "=" * 70)
    print(f"Total findings: {len(findings)}")

    # Return non-zero if P0 findings exist
    return 1 if p0_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
