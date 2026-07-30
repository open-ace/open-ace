"""Validate daily_usage tenant attribution (Issue #1824)

Revision ID: 20260730_001_validate_daily_usage_tenant
Revises: 20260717_004_scope_usage_and_audit_to_tenant
Create Date: 2026-07-30

Issue: #1824

This migration validates that daily_usage.tenant_id is properly populated
and checks for potential conflicts before enforcing the unique constraint.

Note: daily_usage table does NOT have session_id field, so we cannot
infer tenant_id from session→user→tenant path. All historical data has
tenant_id=1 via server_default from the previous migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_001_validate_daily_usage_tenant"
down_revision: str | None = "20260728_001_add_sandbox_effective_policy"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Validate daily_usage tenant attribution."""
    conn = op.get_bind()

    # Check for NULL tenant_id (should not exist due to server_default)
    result = conn.execute(
        sa.text("SELECT COUNT(*) as count FROM daily_usage WHERE tenant_id IS NULL")
    ).fetchone()

    if result and result[0] > 0:
        # Fill NULL tenant_id with default value 1
        conn.execute(sa.text("UPDATE daily_usage SET tenant_id = 1 WHERE tenant_id IS NULL"))
        print(f"Updated {result[0]} rows with NULL tenant_id to tenant_id=1")

    # Check for potential conflicts (same date/tool/host with multiple tenants)
    conflicts = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) as count
            FROM (
                SELECT date, tool_name, host_name
                FROM daily_usage
                GROUP BY date, tool_name, host_name
                HAVING COUNT(DISTINCT COALESCE(tenant_id, 1)) > 1
            )
            """
        )
    ).fetchone()

    if conflicts and conflicts[0] > 0:
        print(
            f"Warning: Found {conflicts[0]} conflict groups (same date/tool/host with multiple tenants)"
        )
        print("Run scripts/check_daily_usage_conflicts.py for details")
        print("Run scripts/resolve_daily_usage_conflicts.py to resolve")
    else:
        print("No conflicts found - unique constraint is safe")


def downgrade() -> None:
    """No downgrade needed for validation migration."""
    pass
