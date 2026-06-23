"""Remove redundant daily_messages indexes

Revision ID: 035_remove_redundant_indexes
Revises: 034_add_paused_at
Create Date: 2026-04-30

This migration removes idx_messages_query_optimized from daily_messages,
which is an exact duplicate of idx_messages_date_tool_host (same columns:
date, tool_name, host_name).

Only drops if the index exists (IF EXISTS) to handle databases where
the index was never created or already removed.

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035_remove_redundant_indexes"
down_revision: Union[str, None] = "034_add_paused_at"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Remove redundant idx_messages_query_optimized index."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP INDEX IF EXISTS idx_messages_query_optimized"))


def downgrade() -> None:
    """Re-create the removed index."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_messages_query_optimized "
                "ON daily_messages (date, tool_name, host_name)"
            )
        )
