"""Normalize tool names in derived tables (daily_stats, hourly_stats, usage_summary)

Revision ID: 039_normalize_derived_tables
Revises: 038_normalize_tool_names
Create Date: 2026-05-06

Complements migration 038 which only covered agent_sessions, daily_messages,
and daily_usage. This migration normalizes tool_name in the pre-aggregated
derived tables.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "039_normalize_derived_tables"
down_revision: Union[str, None] = "038_normalize_tool_names"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

ALIASES_IN = "'qwen-code', 'qwen-code-cli'"


def upgrade() -> None:
    for table in ("daily_stats", "hourly_stats", "usage_summary"):
        op.execute(
            sa.text(f"UPDATE {table} SET tool_name = 'qwen' " f"WHERE tool_name IN ({ALIASES_IN})")
        )
    # Also normalize claude-code in all derived tables
    op.execute(
        sa.text("UPDATE usage_summary SET tool_name = 'claude' " "WHERE tool_name = 'claude-code'")
    )


def downgrade() -> None:
    pass
