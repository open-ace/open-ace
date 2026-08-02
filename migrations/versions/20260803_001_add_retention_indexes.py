"""Add indexes for audit_logs retention queries.

Issue #2188 Phase 0: Add indexes for tenant_id and timestamp columns
to optimize retention cleanup queries.

Revision ID: 20260803_001_add_retention_indexes
Revises: 20260801_001_add_platform_tenant_admin_roles
Create Date: 2026-08-03

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260803_001_add_retention_indexes"
down_revision: str | None = "20260801_001_add_platform_tenant_admin_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add indexes for audit_logs retention queries.

    These indexes optimize the retention cleanup queries that filter by
    tenant_id and timestamp. The composite index (tenant_id, timestamp)
    is particularly important for the cleanup queries.
    """
    # Single column indexes
    op.create_index(
        "idx_audit_logs_tenant_id",
        "audit_logs",
        ["tenant_id"],
    )
    op.create_index(
        "idx_audit_logs_timestamp",
        "audit_logs",
        ["timestamp"],
    )
    # Composite index for tenant-scoped retention queries
    op.create_index(
        "idx_audit_logs_tenant_timestamp",
        "audit_logs",
        ["tenant_id", "timestamp"],
    )


def downgrade() -> None:
    """Remove retention optimization indexes."""
    op.drop_index("idx_audit_logs_tenant_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_logs_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_logs_tenant_id", table_name="audit_logs")
