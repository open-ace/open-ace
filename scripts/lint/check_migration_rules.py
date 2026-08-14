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

  MIG003 — A revision id that has shipped must never disappear. Alembic keys
    history off the ``revision`` string, not the filename: every deployed
    database stores it verbatim in ``alembic_version.version_num``. Delete or
    rewrite an id that is already reachable from the base branch and every
    database stamped with it dies on the next ``upgrade head`` with
    ``Can't locate revision identified by '<id>'`` — and stays stuck for every
    later migration too.

    This is not hypothetical: ``20260731_003_add_proxy_token_terminated_fields``
    was renumbered to ``20260731_004_...`` so a newer migration could take the
    003 slot, wedging every database upgraded in that window. The recovery is
    ``migrations/versions/20260731_003_bridge_renamed_proxy_token_revision.py``.

    Renaming the *file* is fine; only the id matters. If an id genuinely has to
    stop carrying DDL, keep the node and empty its ``upgrade()`` instead of
    deleting it.

The check is AST-based (stdlib only — no new dependency) and opens no database.
MIG003 additionally shells out to ``git`` to read the baseline tree; if git or
the ref is unavailable it is skipped with a note rather than failing, so the
check never blocks on a shallow clone or an unfetched remote.

Usage:
    # Check the canonical migrations/versions/ tree
    python3 scripts/lint/check_migration_rules.py

    # Check an alternate tree (e.g. the synthetic pre-merged tree assembled by
    # the migration-graph CI job)
    python3 scripts/lint/check_migration_rules.py /path/to/migrations/versions

    # Compare released ids against a different baseline (default: origin/main)
    python3 scripts/lint/check_migration_rules.py --baseline-ref origin/develop
    python3 scripts/lint/check_migration_rules.py --skip-released-check

Exit code: 1 if any violation is found, 0 otherwise.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess  # nosec: B404 - fixed git argv, no shell
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

# The CONCURRENTLY keyword signals a raw concurrent DDL statement
# (``CREATE/DROP/REINDEX/REFRESH ... CONCURRENTLY``). We anchor it to a
# preceding DDL verb rather than matching the bare word: a bare
# ``\bCONCURRENTLY\b`` still matches the token inside a data predicate (``%`` is
# a non-word char, so ``WHERE note LIKE '%concurrently%'`` is bounded by word
# boundaries on both sides). Requiring a leading DDL verb in the same literal
# keeps the match on actual DDL while leaving data backfills alone.
#
# The verb set covers every PostgreSQL statement that takes CONCURRENTLY and
# cannot run inside a transaction: CREATE/DROP INDEX, REINDEX, and REFRESH
# MATERIALIZED VIEW. The last has no Alembic ``op.*`` helper, so it can only be
# issued via raw ``op.execute()`` — exactly what MIG002(a) is meant to catch.
_CONCURRENTLY_TOKEN = "CONCURRENTLY"
_CONCURRENTLY_DDL_RE = re.compile(
    r"\b(?:CREATE|DROP|REINDEX|REFRESH)\b.*\b" + re.escape(_CONCURRENTLY_TOKEN) + r"\b",
    re.IGNORECASE | re.DOTALL,
)


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
                if text and _CONCURRENTLY_DDL_RE.search(text):
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


# ---------------------------------------------------------------------------
# MIG003 — released revision ids must not disappear
# ---------------------------------------------------------------------------

# Baseline the working tree's revision ids are compared against. A stale
# baseline only ever knows about *fewer* ids, so it can miss a violation but
# never invent one.
DEFAULT_BASELINE_REF = os.environ.get("MIG003_BASELINE_REF", "origin/main")

# Deliberately retired revision ids. Adding one here asserts that no live
# database is stamped with it. Prefer keeping the node with an empty
# ``upgrade()`` — that costs nothing and cannot be wrong.
RETIRED_REVISION_IDS: frozenset[str] = frozenset()


def _revision_id_from_source(source: str, filename: str = "<baseline>") -> str | None:
    """Extract the module-level ``revision`` id, or None if absent/dynamic.

    Handles both ``revision: str = "..."`` and ``revision = "..."``.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return None

    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target = first.id
        if target != "revision":
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _git(args: list[str]) -> str | None:
    """Run a git command from the project root; None if git/ref is unavailable."""
    try:
        result = subprocess.run(  # nosec: B603 - fixed argv, no shell
            ["git", "-C", str(PROJECT_ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return result.stdout


def _revision_ids_at_ref(ref: str) -> dict[str, str] | None:
    """Map revision id -> path for every migration on ``ref``.

    Returns None when the baseline cannot be read (no git, unknown ref,
    shallow clone), which callers treat as "skip", not "pass".
    """
    listing = _git(["ls-tree", "-r", "--name-only", ref, "--", "migrations/versions"])
    if listing is None:
        return None

    ids: dict[str, str] = {}
    for path in listing.splitlines():
        if not path.endswith(".py") or Path(path).name.startswith("__"):
            continue
        blob = _git(["show", f"{ref}:{path}"])
        if blob is None:
            continue
        rev = _revision_id_from_source(blob, path)
        if rev:
            ids[rev] = path
    return ids


def check_released_revision_ids(
    versions_dir: Path, baseline_ref: str = DEFAULT_BASELINE_REF
) -> list[Violation]:
    """MIG003: every revision id on ``baseline_ref`` must still exist locally."""
    baseline = _revision_ids_at_ref(baseline_ref)
    if baseline is None:
        print(
            f"MIG003: skipped (cannot read migrations from '{baseline_ref}').",
            file=sys.stderr,
        )
        return []
    if not baseline:
        print(
            f"MIG003: skipped ('{baseline_ref}' has no migrations to compare).",
            file=sys.stderr,
        )
        return []

    current: set[str] = set()
    for f in sorted(versions_dir.glob("*.py")):
        if f.name.startswith("__"):
            continue
        try:
            rev = _revision_id_from_source(f.read_text(encoding="utf-8"), str(f))
        except OSError:
            continue
        if rev:
            current.add(rev)

    violations: list[Violation] = []
    for rev, path in sorted(baseline.items()):
        if rev in current or rev in RETIRED_REVISION_IDS:
            continue
        violations.append(
            Violation(
                "MIG003",
                versions_dir,
                0,
                f"revision id {rev!r} exists on {baseline_ref} ({path}) but not in this "
                f"tree. Every database stamped with it will fail 'upgrade head' with "
                f"\"Can't locate revision identified by '{rev}'\". Restore the id — "
                f"renaming the file is fine, changing the id is not. If the DDL moved "
                f"elsewhere, keep the id as a no-op bridge (see "
                f"migrations/versions/20260731_003_bridge_renamed_proxy_token_revision.py).",
            )
        )
    return violations


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
    args = list(argv if argv is not None else sys.argv[1:])

    skip_released = False
    if "--skip-released-check" in args:
        args.remove("--skip-released-check")
        skip_released = True

    baseline_ref = DEFAULT_BASELINE_REF
    if "--baseline-ref" in args:
        i = args.index("--baseline-ref")
        if i + 1 >= len(args):
            print("FAIL: --baseline-ref requires a value", file=sys.stderr)
            return 2
        baseline_ref = args[i + 1]
        del args[i : i + 2]

    positional = [a for a in args if not a.startswith("-")]
    if positional:
        versions_dir = Path(positional[0]).resolve()
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

    if not skip_released:
        all_violations.extend(check_released_revision_ids(versions_dir, baseline_ref))

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

    rules = "MIG001/MIG002" if skip_released else "MIG001/MIG002/MIG003"
    print(f"OK: {len(migration_files)} migration file(s) pass {rules}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
