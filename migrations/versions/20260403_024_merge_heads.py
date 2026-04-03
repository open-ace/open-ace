"""Merge multiple heads

Revision ID: 024_merge_heads
Revises: 022_sessions_list_opt, 023_add_user_request_trend_index
Create Date: 2026-04-03

This migration merges the two heads that branched from 021_postgresql_optimization:
- 022_sessions_list_opt
- 022_add_request_stats_indexes -> 023_add_user_request_trend_index

"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024_merge_heads"
down_revision: Union[str, None] = ("022_sessions_list_opt", "023_add_user_request_trend_index")
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Merge branches - no schema changes needed."""
    pass


def downgrade() -> None:
    """Merge branches - no schema changes needed."""
    pass