"""Add schema_metadata table for database-level guard.

Revision ID: 20260808_001_add_schema_metadata
Revises:
Create Date: 2026-08-08

Issue: #2330 - Database-level guard for schema compatibility
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260808_001_add_schema_metadata"
down_revision: str | None = "20260805_010_acceptance_verification_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add schema_metadata table for database initialization tracking."""
    # Create table with dialect-agnostic syntax
    # Use sa.func.now() or current_timestamp for cross-dialect compatibility
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        # PostgreSQL: use native TIMESTAMP type
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_metadata (
                initialized_at TIMESTAMP NOT NULL DEFAULT NOW(),
                schema_version VARCHAR(64)
            )
            """
        )
    else:
        # SQLite and others: use dialect-agnostic approach
        # SQLite doesn't support DEFAULT NOW() in CREATE TABLE
        op.create_table(
            "schema_metadata",
            sa.Column("initialized_at", sa.DateTime(), nullable=False),
            sa.Column("schema_version", sa.String(64), nullable=True),
        )

    # Check if table is empty before inserting
    # This prevents duplicate inserts on re-runs
    result = conn.execute(sa.text("SELECT COUNT(*) FROM schema_metadata"))
    count = result.scalar()

    if count == 0:
        # Insert initialization record with current timestamp
        # Use datetime.now() for cross-dialect compatibility
        current_time = datetime.now(timezone.utc).isoformat()
        conn.execute(
            sa.text(
                "INSERT INTO schema_metadata (initialized_at, schema_version) "
                "VALUES (:ts, 'baseline_2026_06_23')"
            ),
            {"ts": current_time},
        )


def downgrade() -> None:
    """Remove schema_metadata table."""
    op.execute("DROP TABLE IF EXISTS schema_metadata")