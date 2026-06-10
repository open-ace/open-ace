"""Add hostname validation indexes

Revision ID: 056_add_hostname_validation_indexes
Revises: 055_add_fork_and_cancel_feedback_fields
Create Date: 2026-06-10

This migration adds indexes for hostname validation:
- Conditional index on usage_summary.host_name for valid hostnames only
- Uses CONCURRENTLY option to avoid table locks in production

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "056_add_hostname_validation_indexes"
down_revision: Union[str, None] = "055_add_fork_and_cancel_feedback_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # ============================================
    # usage_summary table - Add conditional index
    # ============================================
    # Create conditional index for valid hostnames only
    # This index speeds up get_all_hosts() queries while filtering out invalid hostnames
    # Uses CONCURRENTLY to avoid locking the table in production

    # PostgreSQL only - conditional index with WHERE clause
    # SQLite doesn't support conditional indexes, so we create a regular index
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL: Create conditional index with CONCURRENTLY
        # CONCURRENTLY requires raw SQL (not supported by create_index)
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_usage_summary_host_name_valid
            ON usage_summary (host_name)
            WHERE host_name IS NOT NULL
              AND host_name != ''
              AND host_name NOT LIKE '<%>'
              AND LENGTH(host_name) BETWEEN 1 AND 253
            """
        )
    else:
        # SQLite: Create regular index (no conditional indexes)
        op.create_index(
            "idx_usage_summary_host_name_valid",
            "usage_summary",
            ["host_name"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop the hostname validation index
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL: Drop index with CONCURRENTLY
        op.execute(
            """
            DROP INDEX CONCURRENTLY IF EXISTS idx_usage_summary_host_name_valid
            """
        )
    else:
        # SQLite: Drop index normally
        op.drop_index("idx_usage_summary_host_name_valid", "usage_summary")
