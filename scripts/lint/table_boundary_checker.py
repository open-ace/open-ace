#!/usr/bin/env python3
"""Table boundary checker — enforces the #1125 data-contract table boundaries.

Two architectural rules (#1125 / docs/cn/workspace-session-data-contract.md):

  TBL001 — Work-page routes must NOT read the ``daily_messages`` analysis fact
           table. Work routes serve the Workspace runtime; per the contract the
           analysis fact table must not participate in Workspace runtime
           display. Detection is two-layered:
             (a) text: any ``daily_messages`` token in a Work route file (incl.
                 comments/strings) is a violation — even a comment is banned so
                 the rule is self-documenting and the guard can't be silenced
                 by "just a comment".
             (b) call-graph: a Work route calling a repo method whose body
                 reads ``daily_messages`` is an indirect violation. This catches
                 ``usage_repo.get_combined_usage`` even though the route file
                 itself is clean. AST-based; covers direct ``repo.method()``
                 calls (the only pattern the Work routes use today).

  TBL002 — Manage-page routes must NOT write ``session_messages`` /
           ``agent_sessions``. Those are Workspace runtime tables owned by
           SessionManager; the Manage/analysis layer may read them but must not
           mutate them. Regex-based DML detection (skips comments).

A violation is signed ``rule|file|symbol`` (no line number, to survive drift),
mirrors the api_security_scanner baseline convention, and the guard exits 1 on
any non-baselined violation so pre-commit / CI block the change. Use
``--baseline`` to print the suppression JSON to stdout (redirect to
``scripts/lint/.table_boundary_baseline`` to regenerate it).

Run:  python3 scripts/lint/table_boundary_checker.py [--baseline]
"""

from __future__ import annotations


from __future__ import annotations


from __future__ import annotations
import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "scripts" / "lint" / ".table_boundary_baseline"

# Work-scope route files (workspace_bp / quota_bp blueprints).
# Assumption: the Work-page frontend only fetches usage data through these two
# blueprints. Other routes that legitimately read daily_messages via
# UsageRepository (admin.py, governance.py, usage.py, report.py) belong to the
# Manage/analysis domain and are correctly excluded. If the Work page ever
# fetches usage from another route, add it here or the guard's coverage
# silently narrows.
WORK_ROUTE_FILES = {
    "app/routes/workspace.py",
    "app/routes/quota.py",
}
# Manage-scope route files (admin_bp / governance_bp blueprints).
MANAGE_ROUTE_FILES = {
    "app/routes/admin.py",
    "app/routes/governance.py",
}

# Repository classes Work routes may call into, mapped to their source files.
# Only the classes that carry daily_messages reads need scanning.
REPO_FILES = {
    "UsageRepository": "app/repositories/usage_repo.py",
    "MessageRepository": "app/repositories/message_repo.py",
}

# Rule B: DML that mutates the Workspace runtime tables. Case-insensitive.
_WRITE_RUNTIME_TABLES_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(session_messages|agent_sessions)\b",
    re.IGNORECASE,
)


@dataclass
class BoundaryViolation:
    """A detected table-boundary violation."""

    rule: str
    file: str
    symbol: str  # route endpoint or repo method name; "" for text-level
    message: str

    def key(self) -> str:
        # Exclude line number so the signature survives code drift.
        return f"{self.rule}|{self.file}|{self.symbol}"


# ---------------------------------------------------------------------------
# Repo call-graph analysis
# ---------------------------------------------------------------------------


def _method_reads_daily_messages(method_node: ast.AST) -> bool:
    """Whether a method/function body references ``daily_messages``.

    Scans string literals (SQL) and identifiers in the body, but excludes the
    docstring: a method that *documents* it avoids the table (e.g.
    get_session_only_usage's "no daily_messages" docstring) must not be flagged.
    Only executable references count.
    """
    # Drop the docstring (first stmt if it's a bare string literal) before walking.
    body = list(getattr(method_node, "body", []) or [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "daily_messages" in node.value:
                    return True
            if isinstance(node, ast.Name) and node.id == "daily_messages":
                return True
    return False


def build_repo_method_table(repo_files: dict[str, str]) -> dict[str, set[str]]:
    """Map repo class name -> set of method names whose body reads daily_messages.

    Returns e.g. {"UsageRepository": {"get_combined_usage", "get_usage_by_date"}}.
    Methods that don't touch the table (get_session_only_usage) are absent.
    """
    readers: dict[str, set[str]] = {cls: set() for cls in repo_files}
    for cls, rel in repo_files.items():
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != cls:
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if _method_reads_daily_messages(item):
                        readers[cls].add(item.name)
            break  # only one class per file by name
    return readers


def collect_work_route_repo_calls(rel_path: str) -> list[tuple[str, str]]:
    """Collect (repo_class, method) calls a Work route makes into repo classes.

    Resolves ``repo_var.method(...)`` where ``repo_var`` was assigned from a
    constructor of a known repo class (``x = UsageRepository()``) OR the class
    name is used directly. Returns the set of called method names per class.
    """
    path = PROJECT_ROOT / rel_path
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []

    # var name -> repo class name, from ``x = UsageRepository()`` assignments.
    var_to_cls: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in REPO_FILES
            ):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        var_to_cls[tgt.id] = node.value.func.id

    calls: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            cls = var_to_cls.get(node.func.value.id)
            if cls:
                calls.append((cls, node.func.attr))
    return calls


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------


def check_tbl001_text(work_files: set[str]) -> list[BoundaryViolation]:
    """TBL001 (a): any ``daily_messages`` text in a Work route file."""
    violations = []
    for rel in work_files:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        violations.extend(_check_tbl001_text_content(path.read_text(encoding="utf-8"), rel))
    return violations


def _check_tbl001_text_content(content: str, rel: str) -> list[BoundaryViolation]:
    """Scan pre-read file content for any 'daily_messages' token.

    Split out so tests can pass synthetic content without touching the filesystem.
    Any occurrence — code, comment, or string — is a violation: the rule bans the
    token outright so it stays self-documenting in Work route files.
    """
    violations = []
    for i, line in enumerate(content.splitlines(), 1):
        if "daily_messages" in line:
            violations.append(
                BoundaryViolation(
                    rule="TBL001",
                    file=rel,
                    symbol="",
                    message=(
                        f"line {i}: Work route references 'daily_messages' "
                        "(analysis fact table must not be read by Workspace "
                        "runtime per #1125)"
                    ),
                )
            )
    return violations


# Test-facing alias.
check_tbl001_text_content = _check_tbl001_text_content


def check_tbl001_callgraph(
    work_files: set[str], readers: dict[str, set[str]]
) -> list[BoundaryViolation]:
    """TBL001 (b): Work route calls a repo method that reads daily_messages."""
    violations = []
    for rel in work_files:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        called = collect_work_route_repo_calls(rel)
        bad = set()
        for cls, method in called:
            if method in readers.get(cls, set()):
                bad.add((cls, method))
        for cls, method in sorted(bad):
            violations.append(
                BoundaryViolation(
                    rule="TBL001",
                    file=rel,
                    symbol=f"{cls}.{method}",
                    message=(
                        f"Work route calls {cls}.{method}() which reads "
                        "'daily_messages' (indirect analysis-table read per #1125)"
                    ),
                )
            )
    return violations


def check_tbl002(manage_files: set[str]) -> list[BoundaryViolation]:
    """TBL002: Manage route must not write session_messages / agent_sessions.

    Inline-DML only: detects ``INSERT/UPDATE/DELETE`` against the runtime
    tables written directly in the route file. Indirect writes (e.g. a Manage
    route calling ``message_repo.save_message`` or SessionManager) are NOT
    covered — there is no write-side call-graph today. This is acceptable
    because admin.py/governance.py currently neither read nor write these
    tables; if that changes, add a write-side call-graph analogous to
    check_tbl001_callgraph.
    """
    violations = []
    for rel in manage_files:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        violations.extend(_check_tbl002_content(content, rel))
    return violations


def _check_tbl002_content(content: str, rel: str) -> list[BoundaryViolation]:
    """Scan pre-read file content for TBL002 DML against runtime tables.

    Split out so tests can pass synthetic content without touching the filesystem.
    """
    violations = []
    for i, line in enumerate(content.splitlines(), 1):
        if line.lstrip().startswith("#"):  # skip comment lines
            continue
        m = _WRITE_RUNTIME_TABLES_RE.search(line)
        if m:
            table = m.group(1)
            violations.append(
                BoundaryViolation(
                    rule="TBL002",
                    file=rel,
                    symbol=table,
                    message=(
                        f"line {i}: Manage route writes '{table}' "
                        "(Workspace runtime table, owned by SessionManager per #1125)"
                    ),
                )
            )
    return violations


# Test-facing alias.
check_tbl002_content = _check_tbl002_content


# ---------------------------------------------------------------------------
# Baseline management
# ---------------------------------------------------------------------------


def load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text())
        return {item["key"] for item in data}
    except (json.JSONDecodeError, KeyError):
        return set()


def generate_baseline(violations: list[BoundaryViolation]) -> None:
    items = [
        {
            "key": v.key(),
            "rule": v.rule,
            "file": v.file,
            "symbol": v.symbol,
            "message": v.message,
        }
        for v in violations
    ]
    print(json.dumps(items, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Table boundary checker (#1125)")
    parser.add_argument(
        "files", nargs="*", help="Ignored (kept for pre-commit compat). Scans fixed route sets."
    )
    parser.add_argument("--baseline", action="store_true", help="Generate baseline to stdout")
    args = parser.parse_args()

    readers = build_repo_method_table(REPO_FILES)
    violations = check_tbl001_text(WORK_ROUTE_FILES)
    violations += check_tbl001_callgraph(WORK_ROUTE_FILES, readers)
    violations += check_tbl002(MANAGE_ROUTE_FILES)

    if args.baseline:
        generate_baseline(violations)
        return 0

    baseline_keys = load_baseline()
    new_violations = [v for v in violations if v.key() not in baseline_keys]

    if new_violations:
        print(f"Found {len(new_violations)} table-boundary violation(s).")
        print(f"({len(baseline_keys)} baseline suppression(s) active)")
        for v in new_violations:
            print(f"  {v.file}: {v.rule} {v.message}")
        return 1

    print(
        f"No new table-boundary violations. ({len(baseline_keys)} baseline suppression(s) active)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
