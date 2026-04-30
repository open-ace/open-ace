"""Add usage_summary table for pre-aggregated dashboard data

Revision ID: 017_add_usage_summary
Revises: 016_add_dashboard_indexes
Create Date: 2026-03-29

This migration creates a summary table to store pre-aggregated usage data
for fast dashboard queries.

Problem:
- /api/summary query takes ~800ms due to full table aggregation
- GROUP BY tool_name requires scanning all 193k+ rows

Solution:
- Create usage_summary table with pre-calculated aggregates
- Update summary when data changes (on upload or scheduled)
- Dashboard queries read from summary table (millisecond response)

Table structure:
- tool_name: Tool identifier
- host_name: Host identifier (NULL for global summary)
- days_count: Number of days with data
- total_tokens: Sum of all tokens
- avg_tokens: Average tokens per day
- total_requests: Total request count
- total_input_tokens: Sum of input tokens
- total_output_tokens: Sum of output tokens
- first_date: Earliest date with data
- last_date: Latest date with data
- updated_at: Last update timestamp

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "017_add_usage_summary"
down_revision: Union[str, None] = "016_add_dashboard_indexes"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    if not _table_exists(conn, "usage_summary"):
        # Create usage_summary table
        op.create_table(
            "usage_summary",
            sa.Column("tool_name", sa.String(50), nullable=False),
            sa.Column("host_name", sa.String(100), nullable=True),  # NULL for global summary
            sa.Column("days_count", sa.Integer, nullable=False, default=0),
            sa.Column("total_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("avg_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("total_requests", sa.Integer, nullable=False, default=0),
            sa.Column("total_input_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("total_output_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("first_date", sa.String(10), nullable=True),
            sa.Column("last_date", sa.String(10), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

        # Create unique constraint for tool_name + host_name
        op.create_unique_constraint(
            "uq_usage_summary_tool_host", "usage_summary", ["tool_name", "host_name"]
        )

        # Create indexes for fast lookup
        op.create_index("idx_usage_summary_tool", "usage_summary", ["tool_name"])
        op.create_index("idx_usage_summary_host", "usage_summary", ["host_name"])


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if _table_exists(conn, "usage_summary"):
        op.drop_index("idx_usage_summary_host", "usage_summary")
        op.drop_index("idx_usage_summary_tool", "usage_summary")
        op.drop_constraint("uq_usage_summary_tool_host", "usage_summary")
        op.drop_table("usage_summary")
