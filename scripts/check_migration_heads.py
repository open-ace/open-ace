#!/usr/bin/env python3
"""
Check that the Alembic migration graph has exactly one head.

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

No database is opened and no ``upgrade()`` is executed; ``get_heads()`` only
parses each migration module's ``revision`` / ``down_revision`` attributes.

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
    heads = ScriptDirectory.from_config(cfg).get_heads()

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
