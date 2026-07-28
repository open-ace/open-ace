#!/usr/bin/env python3
"""
Check that the Alembic migration graph has exactly one head and is resolvable.

A single head is a structural invariant of the migration chain: if two
migrations declare the same ``down_revision`` (e.g. two parallel branches each
forking off the same parent), the chain forks and ``alembic heads`` reports
more than one. Each branch is single-headed in isolation, so this fork only
appears once the branches are merged — this script only catches it when run
against the *merged* migration tree.

It is therefore designed to be run from CI on a pre-merged tree (see
``.github/workflows/migration-graph.yml``), which assembles the base branch's
``migrations/versions/`` together with the PR's migration changes before
invoking this check. The pre-commit hook ``check-migration-heads`` also calls
this script, but it only sees the current working tree and so cannot detect
cross-branch forks — it guards the (rarer) single-branch multi-head case.

No database is opened and no ``upgrade()`` is executed here. In addition to
the single-head assertion, building the revision map surfaces a dangling
``down_revision`` (one pointing at a revision id no migration defines) as a
deterministic failure rather than an unhandled traceback. ``get_heads()``
alone cannot catch a broken/stamped DB row — only a live ``upgrade head``
can, which is why the schema-sync CI job also runs ``alembic upgrade head``
end-to-end on a throwaway database. This script deliberately stays
database-free so it works inside the synthetic pre-merged tree (which has no
``migrations/env.py`` or ``scripts/``).

Usage:
    python3 scripts/check_migration_heads.py
"""

import sys
from pathlib import Path


def main() -> int:
    # alembic is an optional dependency for this check's environments (e.g. the
    # CI lint job runs pre-commit without installing requirements). Import
    # lazily and warn-only when absent — the authoritative check runs in the
    # dedicated migration-graph CI job, mirroring how check-schema-sync.sh
    # degrades when alembic isn't installed.
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:
        print(
            "WARNING: alembic not installed; skipping single-head check. "
            "The migration-graph CI job runs the authoritative check.",
            file=sys.stderr,
        )
        return 0

    # Resolve alembic.ini from the current directory so the check works inside
    # the temporary pre-merged tree assembled by the CI workflow.
    cfg_path = Path("alembic.ini")
    if not cfg_path.exists():
        print(f"FAIL: {cfg_path} not found in {Path.cwd()}", file=sys.stderr)
        return 2

    cfg = Config(str(cfg_path))
    script_dir = ScriptDirectory.from_config(cfg)

    # Building the revision map (which get_heads()/walk_revisions() trigger)
    # raises KeyError when a migration's down_revision points at a revision id
    # that no file defines. Catch that here so the gate fails with a clear
    # message instead of an unhandled traceback. See issue #2101: such a
    # dangling reference is invisible to get_heads() in the source graph (the
    # phantom there lived in a stamped DB row), but when it does appear in the
    # source it must fail this check, not crash opaquely.
    try:
        heads = script_dir.get_heads()
    except KeyError as exc:
        print(
            "FAIL: a migration references a down_revision that is not defined by "
            "any migration file (dangling parent):",
            file=sys.stderr,
        )
        print(f"  unresolved revision id: {exc}", file=sys.stderr)
        print(
            "A revision id was likely renamed or removed without updating its "
            "children. Point each down_revision at a revision id that exists in "
            "migrations/versions/.",
            file=sys.stderr,
        )
        return 1

    if len(heads) != 1:
        print(
            f"FAIL: expected exactly 1 migration head, found {len(heads)}:"
            f" a forked migration chain.",
            file=sys.stderr,
        )
        for head in heads:
            print(f"  - {head}", file=sys.stderr)
        print(
            "Two migrations share a down_revision. Rebase one onto the other so "
            "the chain stays linear (its down_revision must point at the other's "
            "revision, not at their common parent).",
            file=sys.stderr,
        )
        return 1

    print(f"OK: single migration head -> {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
