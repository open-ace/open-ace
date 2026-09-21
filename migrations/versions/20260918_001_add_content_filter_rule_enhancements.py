"""Bridge migration for reverted content filter rule enhancements.

Revision ID: 20260918_001
Revises: 20260911_002_add_users_system_uid
Create Date: 2026-09-21

This migration applies no DDL. It exists so that an already-released revision
id keeps resolving.

What happened
-------------
``20260918_001_add_content_filter_rule_enhancements.py`` shipped on main (PR #3412)
declaring ``revision = "20260918_001"`` with ``down_revision = "20260911_002_add_users_system_uid"``.
It added content filter rule enhancements including approval workflow, test rule
marking, tenant isolation, and priority-based sorting.

The approval workflow design was flawed (Issue #3416):
- Creation and approval both required platform admin role
- The `approval_status` field duplicated the `is_enabled` field's purpose
- No realistic use case for the approval workflow

PR #3416 reverted the approval workflow functionality, but the revision id
must remain to avoid breaking databases that already applied the original
migration.

Why this shape
--------------
Restoring the id as an empty node parented to 20260911_002 puts those databases
back on the graph without re-running DDL::

    20260911_002 -> 20260918_001(this, no-op) -> 20260918_002 -> ...

* A stuck database resolves its stamp here, then applies subsequent migrations.
* A fresh database passes through this no-op and reaches exactly the same schema.

The DDL originally added by this migration has been rolled back by
20260921_001_revert_content_filter_rule_enhancements.py.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "20260918_001"
down_revision: str | None = "20260911_002_add_users_system_uid"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """No-op: the DDL for this id was rolled back by 20260921_001."""


def downgrade() -> None:
    """No-op: nothing was applied here."""
