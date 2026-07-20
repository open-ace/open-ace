#!/usr/bin/env python3
"""Guard the minimum supported Alembic revision before `alembic upgrade head`.

The baseline cutover path (``scripts/cutover_alembic_baseline.py``) was a
one-time compatibility measure for databases that predated the
``baseline_2026_06_23`` lineage. Once every supported environment reached that
baseline (Issue #1215), the cutover was retired. The active migration lineage
now starts at ``baseline_2026_06_23``; a database stamped with any earlier
(pre-baseline) revision id is no longer on a known-good upgrade path.

This checker is invoked by the package/Docker install scripts immediately
before ``alembic upgrade head``. It exits non-zero when the database holds a
pre-baseline (or otherwise unrecognized) revision, printing a clear message so
operators recover (restore a healthy backup or stamp onto the baseline)
instead of hitting an opaque Alembic failure.

Fresh databases (no ``alembic_version`` table) are allowed through; their
schema is created from the baseline snapshot and ``alembic stamp head`` in the
fresh-install path.
"""

from __future__ import annotations


from __future__ import annotations


from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from migrations.baseline import (
    ACTIVE_MIGRATIONS_DIR,
    BASELINE_REVISION,
    read_current_revision,
    version_table_exists,
)
from scripts.shared.db import _get_db_url

_REVISION_RE = re.compile(r"^revision\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_REVISION_RE_FALLBACK = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def collect_active_revision_ids() -> set[str]:
    """Collect revision identifiers from the active post-baseline lineage.

    The baseline migration pins its ``revision`` to the
    ``BASELINE_REVISION`` symbol rather than a literal, so its identifier is
    not picked up here; the caller unions the baseline in explicitly. This
    keeps the allowlist self-maintaining as new post-baseline migrations ship.
    """
    revision_ids: set[str] = set()
    if not ACTIVE_MIGRATIONS_DIR.exists():
        return revision_ids

    for path in ACTIVE_MIGRATIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = _REVISION_RE.search(text) or _REVISION_RE_FALLBACK.search(text)
        if match:
            revision_ids.add(match.group(1))

    return revision_ids


def is_supported_revision(current: str | None, supported: set[str]) -> bool:
    """Return whether ``current`` is on the supported (post-baseline) lineage."""
    return current is not None and current in supported


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override the configured DATABASE_URL")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database_url = args.database_url or _get_db_url()
    supported = collect_active_revision_ids() | {BASELINE_REVISION}

    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            if not version_table_exists(connection):
                # Fresh database: the install path builds the schema from the
                # baseline snapshot and stamps head, so nothing to guard here.
                print("Fresh database (no alembic_version table); skipping revision check.")
                return 0
            current = read_current_revision(connection)
    finally:
        engine.dispose()

    if is_supported_revision(current, supported):
        print(
            f"Database revision '{current}' is on the supported lineage (>= {BASELINE_REVISION})."
        )
        return 0

    if current is None:
        print(
            "ERROR: the alembic_version table exists but has no revision row; "
            "the database cannot be upgraded in this state.\n"
            "Recover by restoring a known-healthy backup that is already on the "
            f"'{BASELINE_REVISION}' lineage, then re-run the upgrade.",
            file=sys.stderr,
        )
    else:
        print(
            f"ERROR: database revision '{current}' is below the minimum supported "
            f"starting point '{BASELINE_REVISION}'.\n"
            "This database predates the baseline cutover and cannot be upgraded "
            "in place.\n"
            "Recover by restoring a known-healthy backup that is already on the "
            f"'{BASELINE_REVISION}' lineage, then re-run the upgrade.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
