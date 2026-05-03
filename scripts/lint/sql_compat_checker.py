#!/usr/bin/env python3
"""
SQL Compatibility Checker for Open ACE.

Detects SQL patterns that are incompatible between PostgreSQL and SQLite,
preventing bugs before they reach production.

Rules (v1 scope):
  - SQL001: Boolean field compared with integer literal in SQL context.
            Should use adapt_boolean_condition() instead.
  - SQL003: LIKE query without escape_like() — wildcard injection risk.

Future scope (not yet implemented):
  - SQL002: Raw ? or %s placeholder (use get_param_placeholder() / adapt_sql())
  - SQL004: f-string SQL interpolation (use parameterized queries)

Usage:
    # Check all Python files under app/ and scripts/
    python3 scripts/lint/sql_compat_checker.py

    # Incremental: check only specific files
    python3 scripts/lint/sql_compat_checker.py app/repositories/user_repo.py

    # Generate baseline (suppress known violations)
    python3 scripts/lint/sql_compat_checker.py --baseline > scripts/lint/.sql_baseline

Exit code: 1 if violations found (not in baseline), 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Boolean field detection – reuse definitions from generate_schema.py
# ---------------------------------------------------------------------------

BOOLEAN_FIELD_PATTERNS = [
    r"^is_",
    r"_enabled$",
    r"^allow_",
    r"^must_",
    r"^can_",
    r"^has_",
]

BOOLEAN_SPECIAL_WORDS = {
    "read",
    "success",
    "acknowledged",
    "verified",
    "confirmed",
    "approved",
    "rejected",
    "completed",
    "active",
}

# Counter fields that look boolean but are NOT
COUNT_FIELD_PATTERNS = [
    r"_count$",
    r"_used$",
    r"_made$",
    r"_limit$",
    r"_quota$",
    r"^total_",
    r"_tokens$",
    r"_users$",
    r"_seconds$",
    r"_requests$",
]


def _is_boolean_field(column_name: str) -> bool:
    """Check if a column name is likely a boolean field."""
    for pat in COUNT_FIELD_PATTERNS:
        if re.search(pat, column_name):
            return False
    for pat in BOOLEAN_FIELD_PATTERNS:
        if re.search(pat, column_name):
            return True
    return column_name in BOOLEAN_SPECIAL_WORDS


# Build a regex alternation of all known boolean field names from schema
# (populated at runtime via _load_boolean_fields_from_schema)
_DYNAMIC_BOOL_FIELDS: set[str] = set()


def _load_boolean_fields_from_schema() -> set[str]:
    """Parse the PostgreSQL schema to discover all BOOLEAN column names."""
    fields: set[str] = set()
    schema_path = Path(__file__).resolve().parent.parent.parent / "schema" / "schema-postgres.sql"
    if not schema_path.exists():
        return fields
    content = schema_path.read_text()
    # Match lines like:  field_name boolean ...
    for m in re.finditer(r"^\s+(\w+)\s+boolean\b", content, re.MULTILINE | re.IGNORECASE):
        col = m.group(1)
        if _is_boolean_field(col):
            fields.add(col)
    return fields


# ---------------------------------------------------------------------------
# SQL001: Boolean comparison with integer literal
# ---------------------------------------------------------------------------

# Matches patterns like: is_active = 1, is_shared = 0, u.is_active = 1
# Also matches intermediate variable pattern: xxx_int = 1 if ... else 0
_BOOL_INT_SQL_RE = re.compile(
    r"(?:^|[\"']\s*|\s)"  # start boundary
    r"(\w+\.)?"  # optional table alias
    r"(\w+)"  # field name (group 2)
    r"\s*=\s*[01]\b"  # = 0 or = 1
    r"(?!\s*if\b)",  # NOT Python ternary
)

# Intermediate variable pattern: is_active_int = 1 if is_active else 0
_INT_VAR_RE = re.compile(
    r"(\w+)\s*=\s*[01]\s+if\s+\w+\s+else\s+[01]",
)


def _check_sql001(content: str, filepath: str) -> list[dict]:
    """SQL001: Boolean field compared with integer literal in SQL context."""
    violations: list[dict] = []

    lines = content.splitlines()
    in_sql_context = False

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comment-only lines
        if stripped.startswith("#"):
            continue

        # Check intermediate variable pattern first (no SQL context needed)
        for m in _INT_VAR_RE.finditer(line):
            var_name = m.group(1)
            # Extract possible field name from var like is_active_int → is_active
            base = var_name.replace("_int", "").replace("_val", "")
            if base in _DYNAMIC_BOOL_FIELDS or _is_boolean_field(base):
                violations.append(
                    {
                        "rule": "SQL001",
                        "file": filepath,
                        "line": lineno,
                        "message": f"Boolean value converted to int variable '{var_name}' — use adapt_boolean_value() or adapt_boolean_condition()",
                    }
                )

        # Track SQL string boundaries (triple-quoted and single-quoted)
        # Heuristic: if line contains SQL keywords + quote, it's likely SQL
        has_sql_keyword = bool(
            re.search(
                r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|FROM|SET|AND|OR|JOIN|GROUP BY|ORDER BY|LIMIT)\b",
                stripped,
                re.IGNORECASE,
            )
        )

        if has_sql_keyword:
            in_sql_context = True

        if not in_sql_context and not has_sql_keyword:
            continue

        # Check for adapt_boolean_condition in the line — allowed
        if "adapt_boolean_condition" in line:
            continue

        # Check for boolean = integer pattern
        for m in _BOOL_INT_SQL_RE.finditer(line):
            field_name = m.group(2)
            if field_name in _DYNAMIC_BOOL_FIELDS or _is_boolean_field(field_name):
                violations.append(
                    {
                        "rule": "SQL001",
                        "file": filepath,
                        "line": lineno,
                        "message": f"Boolean field '{field_name}' compared with integer literal — use adapt_boolean_condition()",
                    }
                )

        # Reset SQL context if line ends a multi-line string
        if stripped.endswith('")') or stripped.endswith("')") or stripped.endswith('")  ;'):
            in_sql_context = False

    return violations


# ---------------------------------------------------------------------------
# SQL003: LIKE without escape_like()
# ---------------------------------------------------------------------------

# Matches various LIKE patterns: LIKE ?, LIKE %s, LIKE {_param()}, LIKE {p}
_LIKE_PATTERN_RE = re.compile(
    r"LIKE\s+(?:\?|%s|\{[^}]*\})",
    re.IGNORECASE,
)


def _check_sql003(content: str, filepath: str) -> list[dict]:
    """SQL003: LIKE query without escape_like() — wildcard injection risk."""
    violations: list[dict] = []

    lines = content.splitlines()

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comment-only lines
        if stripped.startswith("#"):
            continue

        # Skip if escape_like is already used in this line or nearby
        # Check a window of context around this line
        start = max(0, lineno - 3)
        end = min(len(lines), lineno + 2)
        context = "\n".join(lines[start:end])

        if "escape_like" in context:
            continue

        # Skip the check file itself
        if "sql_compat_checker" in filepath:
            continue

        for m in _LIKE_PATTERN_RE.finditer(line):
            violations.append(
                {
                    "rule": "SQL003",
                    "file": filepath,
                    "line": lineno,
                    "message": "LIKE query without escape_like() — special characters in user input will be treated as wildcards",
                }
            )
            break  # One violation per line is enough

    return violations


# ---------------------------------------------------------------------------
# Baseline management
# ---------------------------------------------------------------------------

BASELINE_PATH = Path(__file__).resolve().parent / ".sql_baseline"


def _load_baseline() -> set[str]:
    """Load baseline suppressions (JSONL format: one violation signature per line)."""
    if not BASELINE_PATH.exists():
        return set()
    signatures: set[str] = set()
    for line in BASELINE_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
            sig = f"{entry.get('rule')}:{entry.get('file')}:{entry.get('line')}"
            signatures.add(sig)
        except json.JSONDecodeError:
            # Legacy format: plain signature string
            signatures.add(line)
    return signatures


def _violation_signature(v: dict) -> str:
    return f"{v['rule']}:{v['file']}:{v['line']}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def check_file(filepath: Path) -> list[dict]:
    """Run all checks on a single file."""
    content = filepath.read_text()
    fp_str = str(filepath)
    violations: list[dict] = []
    violations.extend(_check_sql001(content, fp_str))
    violations.extend(_check_sql003(content, fp_str))
    return violations


def main() -> None:
    global _DYNAMIC_BOOL_FIELDS

    parser = argparse.ArgumentParser(description="SQL Compatibility Checker for Open ACE")
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific files to check (default: scan app/ and scripts/)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Generate baseline file (print violations as JSONL to stdout)",
    )
    args = parser.parse_args()

    # Load boolean fields from schema
    _DYNAMIC_BOOL_FIELDS = _load_boolean_fields_from_schema()

    # Collect files to check
    project_root = Path(__file__).resolve().parent.parent.parent

    if args.files:
        files: list[Path] = [Path(f).resolve() for f in args.files]
    else:
        files = []
        for pattern_dir in ("app", "scripts"):
            base = project_root / pattern_dir
            if base.exists():
                files.extend(base.rglob("*.py"))

    # Run checks
    all_violations: list[dict] = []
    for f in files:
        if f.name == "sql_compat_checker.py" or f.name == "test_sql_compat_checker.py":
            continue
        all_violations.extend(check_file(f))

    # Baseline mode: dump all violations
    if args.baseline:
        for v in all_violations:
            print(json.dumps(v, ensure_ascii=False))
        return

    # Normal mode: filter out baseline
    baseline = _load_baseline()
    new_violations = [v for v in all_violations if _violation_signature(v) not in baseline]

    # Report
    for v in new_violations:
        # Make path relative to project root for readability
        try:
            rel_path = str(Path(v["file"]).relative_to(project_root))
        except ValueError:
            rel_path = v["file"]
        print(f"{rel_path}:{v['line']}: {v['rule']} {v['message']}")

    if new_violations:
        print(f"\nFound {len(new_violations)} violation(s).", file=sys.stderr)
        if baseline:
            print(
                f"({len(all_violations) - len(new_violations)} baseline suppression(s) active)",
                file=sys.stderr,
            )
        sys.exit(1)
    else:
        if all_violations:
            print(
                f"All {len(all_violations)} violation(s) suppressed by baseline.", file=sys.stderr
            )
        else:
            print("No SQL compatibility violations found.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
