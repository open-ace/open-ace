"""Add parse_failure_records table for file change parser

Issue #2589: File change panel configuration for detecting file/folder operations.

This migration adds:
- parse_failure_records table to track parsing failures
- Indexes for efficient queries
- Cleanup support via created_at timestamp

Revision ID: 20260814_001
Revises: 20260812_001_add_token_version
Create Date: 2026-08-14

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260814_001"
down_revision = "20260812_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add parse_failure_records table."""
    # Get database type for conditional logic
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Check if table already exists (concurrent migration safety)
    inspector = sa.inspect(bind)
    table_names = inspector.get_table_names()

    if "parse_failure_records" in table_names:
        # Table already exists, skip creation (idempotent)
        return

    # Create table with appropriate columns
    if dialect == "sqlite":
        # SQLite version
        op.create_table(
            "parse_failure_records",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("tool_use_id", sa.String(128), nullable=False),
            sa.Column("tool_name", sa.String(64), nullable=False),
            sa.Column("tool_input", sa.Text(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_retry_at", sa.DateTime(), nullable=True),
            sa.Column("resolved", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    else:
        # PostgreSQL version
        op.create_table(
            "parse_failure_records",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("tool_use_id", sa.String(128), nullable=False),
            sa.Column("tool_name", sa.String(64), nullable=False),
            sa.Column("tool_input", sa.Text(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column(
                "timestamp", sa.DateTime(timezone=True), nullable=False
            ),
            sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "last_retry_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("resolved", sa.Boolean(), server_default="false", nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False
            ),
        )

    # Create indexes for efficient queries
    # Index on session_id for session-scoped queries
    op.create_index(
        "idx_parse_failure_session",
        "parse_failure_records",
        ["session_id"],
        unique=False,
    )

    # Index on timestamp for time-based queries
    op.create_index(
        "idx_parse_failure_timestamp",
        "parse_failure_records",
        ["timestamp"],
        unique=False,
    )

    # Index on resolved for finding unresolved failures
    op.create_index(
        "idx_parse_failure_unresolved",
        "parse_failure_records",
        ["resolved"],
        unique=False,
        postgresql_where=sa.text("resolved = false"),
    )

    # Index on created_at for cleanup queries
    op.create_index(
        "idx_parse_failure_created_at",
        "parse_failure_records",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove parse_failure_records table."""
    # Drop indexes
    op.drop_index("idx_parse_failure_created_at", table_name="parse_failure_records")
    op.drop_index("idx_parse_failure_unresolved", table_name="parse_failure_records")
    op.drop_index("idx_parse_failure_timestamp", table_name="parse_failure_records")
    op.drop_index("idx_parse_failure_session", table_name="parse_failure_records")

    # Drop table
    op.drop_table("parse_failure_records")
