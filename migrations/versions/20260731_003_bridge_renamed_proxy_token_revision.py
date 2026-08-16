"""Re-home the orphaned revision id 20260731_003_add_proxy_token_terminated_fields.

Revision ID: 20260731_003_add_proxy_token_terminated_fields
Revises: 20260731_002_add_ci_repair_transient_retries
Create Date: 2026-08-14

This migration applies no DDL. It exists so that an already-released revision
id keeps resolving.

What happened
-------------
``20260731_003_add_proxy_token_terminated_fields.py`` shipped on main (commit
05bcca72) declaring ``revision = "20260731_003_add_proxy_token_terminated_fields"``
with ``down_revision = "20260731_002_add_ci_repair_transient_retries"``. It was
later renumbered to ``20260731_004_add_proxy_token_terminated_fields`` so that
``20260731_003_add_teams_sync_source_indexes`` could take the 003 slot.

Alembic keys history off the revision **id**, not the filename, so the rename
deleted a node that databases were already stamped with. Every deployment that
ran ``upgrade head`` inside that window now has
``alembic_version.version_num = '20260731_003_add_proxy_token_terminated_fields'``
and fails with ``Can't locate revision identified by
'20260731_003_add_proxy_token_terminated_fields'`` -- not just for this
migration, but for every migration after it. The database is wedged.

Why this shape
--------------
Restoring the id as an empty node parented to 002 puts those databases back on
the graph without re-running the DDL they already applied::

    002 -> 003(this, no-op) -> 003_teams_sync_source_indexes -> 004_proxy_token...

* A stuck database resolves its stamp here, then applies the teams index
  migration (which it never saw) and finally 004. 004 already guards every
  ``add_column`` with an inspector check, so the proxy-token columns it applied
  under the old id are not added twice.
* A fresh database passes through this no-op and reaches exactly the same
  schema.

Renaming ``20260731_004_...`` back is NOT an option: that id has shipped too,
and renaming it would strand a second set of databases the same way.

The lint added alongside this file (``scripts/lint/check_migration_rules.py``,
rule MIG003) rejects any future edit that removes or rewrites a revision id
that is already reachable from origin/main.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "20260731_003_add_proxy_token_terminated_fields"
down_revision: str | None = "20260731_002_add_ci_repair_transient_retries"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """No-op: the DDL for this id lives in 20260731_004_add_proxy_token_terminated_fields."""


def downgrade() -> None:
    """No-op: nothing was applied here."""
