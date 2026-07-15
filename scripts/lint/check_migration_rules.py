#!/usr/bin/env python3
"""
Migration Authoring Rules Checker for Open ACE.

Statically enforces the repository-level migration policy that is easy for a
human (or an autonomous agent) to miss and hard to infer from local unit tests
alone (Issue #1704). Two failure modes are codified here:

  MIG001 — Migration files must not import ``app.*`` runtime modules.
    The migration-graph CI job and ``ScriptDirectory.get_heads()`` load each
    migration module from a synthetic pre-merged tree that does NOT contain the
    ``app/`` package. A migration that does ``from app.xxx import ...`` therefore
    fails to import there, breaking the single-head check with an opaque error
    even though every local test passes. Migrations must stay decoupled from the
    runtime: operate via ``alembic.op`` / ``sqlalchemy`` / introspection queries
    and the sibling ``migrations.baseline`` helper only.

    Exception: an import guarded by ``if TYPE_CHECKING:`` is allowed, since it
    is never executed at import time and so cannot break module loading. This
    keeps the check aligned with the *runtime* failure it prevents.

  MIG002 — PostgreSQL ``CONCURRENTLY`` index operations must use the single
    approved pattern. ``CREATE INDEX CONCURRENTLY`` cannot run inside a
    transaction block, so it must be issued via Alembic's ``autocommit_block()``
    context manager together with the ``postgresql_concurrently=True`` dialect
    kwarg. Two mistakes are caught:

      (a) Raw concurrent DDL emitted through ``op.execute(...)``,
          ``connection.execute(...)`` or ``sa.text(...)`` with a string literal
          containing ``CONCURRENTLY``. Raw SQL bypasses autocommit handling and
          raises ``ACTIVE SQL TRANSACTION`` inside Alembic's transaction.
      (b) ``postgresql_concurrently=True`` passed to ``op.create_index`` /
          ``op.drop_index`` while NOT nested inside an ``autocommit_block()``
          ``with`` statement. The kwarg issues ``... CONCURRENTLY`` which is only
          valid outside a transaction.

The correct template (see docs/en/DATABASE-CONVENTIONS.md) is::

    if _is_postgresql():
        with op.get_context().autocommit_block():
            op.create_index(NAME, TABLE, COLS, postgresql_concurrently=True)
    else:
        op.create_index(NAME, TABLE, COLS)

The check is AST-based (stdlib only — no new dependency) and opens no database.

Usage:
    # Check the canonical migrations/versions/ tree
    python3 scripts/lint/check_migration_rules.py

    # Check an alternate tree (e.g. the synthetic pre-merged tree assembled by
    # the migration-graph CI job)
    python3 scripts/lint/check_migration_rules.py /path/to/migrations/versions

Exit code: 1 if any violation is found, 0 otherwise.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root – used for the default migrations/versions/ path
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_VERSIONS_DIR = PROJECT_ROOT / "migrations" / "versions"

# ``op.create_index`` / ``op.drop_index`` accept this dialect kwarg. We track it
# via AST so the check does not depend on import aliasing for the kwarg name
# itself (it is always a literal ``postgresql_concurrently`` keyword argument).
_CONCURRENT_KWARG = "postgresql_concurrently"

# Calls whose string-literal argument is inspected for raw CONCURRENTLY DDL.
# These are matched by trailing attribute name so that ``op.execute``,
# ``conn.execute``, ``connection.execute`` and ``sa.text`` are all covered
# regardless of how the receiver is imported/aliased.
_RAW_DDL_CALLS = {"execute", "text"}

# Substrings that indicate a raw concurrent DDL statement. ``CREATE/DROP/REINDEX
# ... CONCURRENTLY`` and the bare ``CONCURRENTLY`` keyword are the signals.
_CONCURRENTLY_TOKEN = "CONCURRENTLY"


@dataclass(frozen=True)
class Violation:
    """A single rule violation."""

    rule: str
    file: Path
    line: int
    message: str

    def format(self) -> str:
        try:
            rel = self.file.relative_to(PROJECT_ROOT)
        except ValueError:
            rel = self.file
        return f"{rel}:{self.line}: {self.rule} {self.message}"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_type_checking_guard(node: ast.AST) -> bool:
    """Return True if ``node`` is an ``if TYPE_CHECKING:`` guard.

    Recognizes both ``if TYPE_CHECKING:`` (Name) and ``if typing.TYPE_CHECKING:``
    (Attribute), for any name the symbol is imported under.
    """
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _module_is_app(module_name: str) -> bool:
    """Return True for ``app`` or any ``app.`` submodule import."""
    return module_name == "app" or module_name.startswith("app.")


def _is_ancestor_within_type_checking(node: ast.AST, parents: list[ast.AST]) -> bool:
    """Return True if ``node`` is (transitively) inside a TYPE_CHECKING block.

    ``parents`` is the ancestor chain of ``node`` (outermost first, immediate
    parent last), as produced by ``ast.walk``-style tracking.
    """
    return any(_is_type_checking_guard(p) for p in parents)


def _string_literal(text: str) -> str | None:
    """Return the Python string value if ``text`` is a string literal node."""
    if isinstance(text, ast.Constant) and isinstance(text.value, str):
        return text.value
    return None


def _call_name(call: ast.Call) -> str | None:
    """Return the bare tail name of a call, e.g. ``op.execute`` -> ``execute``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _parents_contain_autocommit_block(parents: list[ast.AST]) -> bool:
    """Return True if any ancestor ``With`` item calls ``autocommit_block()``."""
    for p in parents:
        if isinstance(p, ast.With):
            for item in p.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and _call_name(ctx) == "autocommit_block":
                    return True
    return False


def _has_concurrent_kwarg(call: ast.Call) -> bool:
    """Return True if ``call`` passes ``postgresql_concurrently=True`` (or any truthy)."""
    return any(kw.arg == _CONCURRENT_KWARG for kw in call.keywords)


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------


def _check_mig001(
    tree: ast.Module, filepath: Path, parents: dict[int, list[ast.AST]]
) -> list[Violation]:
    """MIG001: no runtime ``app.*`` imports except under ``if TYPE_CHECKING:``."""
    violations: list[Violation] = []

    for node in ast.walk(tree):
        modules: list[tuple[str, ast.stmt]]
        if isinstance(node, ast.Import):
            modules = [(alias.name, node) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            modules = [(node.module, node)]
        else:
            continue

        for module_name, imp_node in modules:
            if not _module_is_app(module_name):
                continue
            chain = parents.get(id(imp_node), [])
            if _is_ancestor_within_type_checking(imp_node, chain):
                continue
            violations.append(
                Violation(
                    rule="MIG001",
                    file=filepath,
                    line=imp_node.lineno,
                    message=(
                        f"Migration imports runtime module '{module_name}'. "
                        "Migrations are loaded from a synthetic tree without the app/ "
                        "package (migration-graph CI), so this breaks module loading. "
                        "Use alembic.op / sqlalchemy / migrations.baseline instead, "
                        "or guard the import with `if TYPE_CHECKING:`."
                    ),
                )
            )

    return violations


def _check_mig002(
    tree: ast.Module, filepath: Path, parents: dict[int, list[ast.AST]]
) -> list[Violation]:
    """MIG002: enforce the single approved CONCURRENTLY pattern."""
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        chain = parents.get(id(node), [])

        # --- MIG002 (a): raw CONCURRENTLY DDL via execute()/text() -----------
        if name in _RAW_DDL_CALLS:
            for arg in node.args:
                text = _string_literal(arg)
                if text and _CONCURRENTLY_TOKEN in text.upper():
                    violations.append(
                        Violation(
                            rule="MIG002",
                            file=filepath,
                            line=node.lineno,
                            message=(
                                "Raw 'CONCURRENTLY' DDL emitted via execute()/text() "
                                "cannot run inside an Alembic transaction. Use "
                                "op.create_index/drop_index(..., postgresql_concurrently=True) "
                                "wrapped in `with op.get_context().autocommit_block():` instead."
                            ),
                        )
                    )
            continue

        # --- MIG002 (b): postgresql_concurrently= must be in autocommit_block -
        if name in {"create_index", "drop_index"} and _has_concurrent_kwarg(node):
            if not _parents_contain_autocommit_block(chain):
                violations.append(
                    Violation(
                        rule="MIG002",
                        file=filepath,
                        line=node.lineno,
                        message=(
                            "postgresql_concurrently=True passed outside an "
                            "autocommit_block(). Wrap the op.create_index/drop_index "
                            "call in `with op.get_context().autocommit_block():`."
                        ),
                    )
                )

    return violations


def _build_parent_map(tree: ast.Module) -> dict[int, list[ast.AST]]:
    """Map each node id to its ancestor chain (outermost first, immediate parent last)."""
    parents: dict[int, list[ast.AST]] = {}

    def visit(node: ast.AST, chain: list[ast.AST]) -> None:
        parents[id(node)] = chain
        new_chain = [*chain, node]
        for child in ast.iter_child_nodes(node):
            visit(child, new_chain)

    visit(tree, [])
    return parents


def check_file(filepath: Path) -> list[Violation]:
    """Run all migration authoring rules on a single file."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError as exc:
        return [Violation("MIG000", filepath, 0, f"Could not read file: {exc}")]

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        return [Violation("MIG000", filepath, exc.lineno or 0, f"Syntax error: {exc.msg}")]

    parents = _build_parent_map(tree)
    violations: list[Violation] = []
    violations.extend(_check_mig001(tree, filepath, parents))
    violations.extend(_check_mig002(tree, filepath, parents))
    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if args:
        versions_dir = Path(args[0]).resolve()
    else:
        versions_dir = DEFAULT_VERSIONS_DIR

    if not versions_dir.is_dir():
        print(f"FAIL: migrations versions dir not found: {versions_dir}", file=sys.stderr)
        return 2

    migration_files = sorted(versions_dir.glob("*.py"))
    # Exclude __init__ / non-migration modules if present.
    migration_files = [f for f in migration_files if not f.name.startswith("__")]

    if not migration_files:
        print(f"FAIL: no migration files found under {versions_dir}", file=sys.stderr)
        return 2

    all_violations: list[Violation] = []
    for f in migration_files:
        all_violations.extend(check_file(f))

    for v in all_violations:
        print(v.format(), file=sys.stderr)

    if all_violations:
        print(
            f"\nFound {len(all_violations)} migration rule violation(s).",
            file=sys.stderr,
        )
        print(
            "See docs/en/DATABASE-CONVENTIONS.md -> Migration Authoring Rules.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(migration_files)} migration file(s) pass MIG001/MIG002.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
