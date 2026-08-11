"""Backfill default tenant for non-admin users with NULL tenant_id

Revision ID: 20260720_001_backfill_notenant_users
Revises: 20260719_002_add_tenant_id_to_analysis_tables
Create Date: 2026-07-20

Issue: #1888

A non-admin user with ``tenant_id IS NULL`` is non-functional: the
fail-closed tenant gate in :func:`require_tenant_scope` (Issue #1775)
denies every write/query endpoint with ``403 Tenant scope required``,
so the user can log in and see an empty project list (GET is exempted
by Issue #1859) but cannot create projects, sessions, API keys, etc.

Every ``create_user`` call site already defaults ``tenant_id=1`` (admin
panel, SSO provisioning, Feishu/DingTalk org sync, tenant-onboarding
admin). The only way a non-admin ends up with ``NULL`` is:

  * the row predates the tenant_id column (created before the 2026-06-23
    baseline, which is a no-op when ``users`` already exists), or
  * direct DB manipulation / a deleted tenant (``ON DELETE SET NULL``).

This migration backfills such stranded non-admins to the default tenant
(id=1, seeded by ``scripts/init_db.py``) so they become functional again
without weakening the route-layer fail-closed guarantee. Admins are left
alone — ``NULL`` is legitimate for them (global scope).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision: str = "20260720_001_backfill_notenant_users"
down_revision: str | None = "20260719_002_add_tenant_id_to_analysis_tables"
branch_labels: str | None = None
depends_on: str | None = None


def backfill_null_tenant_users(conn) -> int:
    """Assign stranded non-admins to the default tenant (id=1).

    Returns the number of rows updated. Exposed as a module-level helper so
    tests (and a future operational script) can invoke it against an
    arbitrary connection without standing up the full Alembic context.

    Only non-admins are touched: admins may legitimately have ``NULL``
    ``tenant_id`` (global scope). The default tenant must exist — otherwise
    the backfill is a no-op so we never create dangling FK rows.
    """
    default_tenant = conn.execute(sa.text("SELECT id FROM tenants WHERE id = 1")).scalar()

    if default_tenant is None:
        # Nothing safe to backfill to — leave rows untouched and let the
        # fail-closed gate keep denying until an operator fixes the data.
        logger.warning(
            "backfill_null_tenant_users: default tenant (id=1) not found; "
            "skipping backfill of NULL tenant_id users."
        )
        return 0

    result = conn.execute(sa.text("""
            UPDATE users
            SET tenant_id = 1
            WHERE tenant_id IS NULL
              AND COALESCE(role, 'user') <> 'admin'
            """))
    # ``result.rowcount`` may be -1 on some drivers; log best-effort.
    try:
        affected = result.rowcount
    except Exception:  # pragma: no cover - driver-specific
        affected = -1
    if affected and affected > 0:
        logger.info(
            "backfill_null_tenant_users: assigned %s non-admin user(s) "
            "with NULL tenant_id to the default tenant (id=1).",
            affected,
        )
    return affected or 0


def upgrade() -> None:
    """Assign stranded non-admins to the default tenant (id=1)."""
    backfill_null_tenant_users(op.get_bind())


def downgrade() -> None:
    """Downgrade is a no-op — we cannot reconstruct the original NULLs."""
    pass
