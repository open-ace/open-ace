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
    """Add partial indexes for organization sync performance.

    These indexes optimize the _load_synced_teams queries in
    feishu_org_sync.py and dingtalk_org_sync.py by filtering
    at the database level instead of in Python.
    """
    if _is_postgresql():
        # PostgreSQL: Create partial indexes with JSON expressions using proper pattern
        # Note: settings column is TEXT, so we must cast to jsonb before using ->>
        with op.get_context().autocommit_block():
            # Feishu partial index
            # Note: Using op.create_index with postgresql_concurrently=True
            # for proper CONCURRENTLY handling per linter rule MIG002
            op.create_index(
                "idx_teams_feishu_sync",
                "teams",
                [
                    sa.text("(settings::jsonb->>'sync_source')"),
                    sa.text("(settings::jsonb->>'feishu_department_id')"),
                ],
                postgresql_where=sa.text("settings::jsonb->>'sync_source' = 'feishu'"),
                postgresql_concurrently=True,
            )

            # DingTalk partial index
            op.create_index(
                "idx_teams_dingtalk_sync",
                "teams",
                [
                    sa.text("(settings::jsonb->>'sync_source')"),
                    sa.text("(settings::jsonb->>'dingtalk_department_id')"),
                ],
                postgresql_where=sa.text("settings::jsonb->>'sync_source' = 'dingtalk'"),
                postgresql_concurrently=True,
            )
    else:
        # SQLite fallback: Regular index (partial indexes not well supported)
        # Note: SQLite may not effectively use this index without partial index support
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
    """Remove organization sync performance indexes."""
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS idx_teams_feishu_sync")
        op.execute("DROP INDEX IF EXISTS idx_teams_dingtalk_sync")
    else:
        try:
            op.drop_index("idx_teams_sync_source", table_name="teams")
        except Exception:
            pass
