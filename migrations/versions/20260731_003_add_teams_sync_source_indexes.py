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


def _index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index already exists (for idempotency)."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        result = bind.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"),
            {"name": index_name},
        )
        return result.fetchone() is not None
    else:
        # PostgreSQL
        result = bind.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
            {"name": index_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Add index for organization sync performance.

    This index optimizes the _load_synced_teams queries in
    feishu_org_sync.py and dingtalk_org_sync.py by filtering
    at the database level instead of in Python.

    Note: We use a simple index on sync_source rather than partial indexes
    because partial indexes with JSON expressions have compatibility issues
    between PostgreSQL and SQLite schema snapshots.
    """
    # Check if index already exists (idempotency for schema.sql bootstrap)
    if _index_exists("teams", "idx_teams_sync_source"):
        return

    # Create simple index on sync_source for both PostgreSQL and SQLite
    if _is_postgresql():
        # PostgreSQL: Cast settings to jsonb before extracting sync_source
        op.create_index(
            "idx_teams_sync_source",
            "teams",
            [sa.text("(settings::jsonb->>'sync_source')")],
        )
    else:
        # SQLite: Use json_extract for compatibility with SQLite < 3.38
        # (->> operator is only available in SQLite 3.38+)
        op.create_index(
            "idx_teams_sync_source",
            "teams",
            [sa.text("(json_extract(settings, '$.sync_source'))")],
        )


def downgrade() -> None:
    """Remove organization sync performance index."""
    op.execute("DROP INDEX IF EXISTS idx_teams_sync_source")
