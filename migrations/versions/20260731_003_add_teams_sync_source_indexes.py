"""Add indexes for teams sync_source performance optimization.

Issue #2174 F1/F5: Optimize organization sync full table scan.

Revision ID: 20260731_003_add_teams_sync_source_indexes
Revises: 20260731_002_add_ci_repair_transient_retries
Create Date: 2026-07-31

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260731_003_add_teams_sync_source_indexes"
down_revision: str | None = "20260731_002_add_ci_repair_transient_retries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """Check if we're running on PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Add index for organization sync performance.

    This index optimizes the _load_synced_teams queries in
    feishu_org_sync.py and dingtalk_org_sync.py by filtering
    at the database level instead of in Python.

    Note: We use a simple index on sync_source rather than partial indexes
    because partial indexes with JSON expressions have compatibility issues
    between PostgreSQL and SQLite schema snapshots.
    """
    # Create simple index on sync_source for both PostgreSQL and SQLite
    if _is_postgresql():
        # PostgreSQL: Cast settings to jsonb before extracting sync_source
        op.create_index(
            "idx_teams_sync_source",
            "teams",
            [sa.text("(settings::jsonb->>'sync_source')")],
        )
    else:
        # SQLite: Use ->> operator directly (no cast needed)
        try:
            op.create_index(
                "idx_teams_sync_source",
                "teams",
                [sa.text("(settings->>'sync_source')")],
            )
        except Exception:
            # SQLite may not support JSON expression indexes
            pass


def downgrade() -> None:
    """Remove organization sync performance index."""
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS idx_teams_sync_source")
    else:
        try:
            op.drop_index("idx_teams_sync_source", table_name="teams")
        except Exception:
            pass
