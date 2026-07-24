"""Add expired_at column to proxy_token_jtis

Revision ID: 20260724_001_add_expired_at_to_proxy_token_jtis
Revises: 20260722_001_add_llm_proxy_resolved_ips
Create Date: 2026-07-24

Issue: #1822
Adds expired_at field to proxy_token_jtis for audit trail:
- expired_at: timestamp when a single-use token was marked as expired
- This distinguishes 'issued+expired unused' from 'consumed' tokens
"""

import logging

import sqlalchemy as sa
from alembic import op

# Revision identifiers
revision: str = "20260724_001_add_expired_at_to_proxy_token_jtis"
down_revision: str | None = "20260722_001_add_llm_proxy_resolved_ips"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Add expired_at column to proxy_token_jtis table."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Check if table exists
    if "proxy_token_jtis" not in set(inspector.get_table_names()):
        logger.info("Table proxy_token_jtis does not exist, skipping migration")
        return

    # Check if column already exists (idempotency)
    columns = [col["name"] for col in inspector.get_columns("proxy_token_jtis")]
    if "expired_at" in columns:
        logger.info("Column expired_at already exists in proxy_token_jtis, skipping")
        return

    # Add expired_at column (nullable for backward compatibility)
    logger.info("Adding expired_at column to proxy_token_jtis")
    op.add_column(
        "proxy_token_jtis",
        sa.Column("expired_at", sa.TIMESTAMP(), nullable=True),
    )

    # Add index for cleanup queries
    # PostgreSQL: use partial index (only index non-NULL values)
    # SQLite: use regular index (partial indexes not supported)
    dialect = connection.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_proxy_token_jtis_expired_at
            ON proxy_token_jtis(expired_at)
            WHERE expired_at IS NOT NULL
            """
        )
    else:
        op.create_index(
            "idx_proxy_token_jtis_expired_at",
            "proxy_token_jtis",
            ["expired_at"],
        )

    logger.info("Migration 20260724_001 completed: expired_at added to proxy_token_jtis")


def downgrade() -> None:
    """Remove expired_at column from proxy_token_jtis table."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Check if table exists
    if "proxy_token_jtis" not in set(inspector.get_table_names()):
        return

    # Check if column exists
    columns = [col["name"] for col in inspector.get_columns("proxy_token_jtis")]
    if "expired_at" not in columns:
        return

    dialect = connection.dialect.name

    # Drop index
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_proxy_token_jtis_expired_at")
    else:
        op.drop_index("idx_proxy_token_jtis_expired_at", "proxy_token_jtis")

    # Drop column
    # Note: SQLite doesn't support DROP COLUMN in older versions
    # For SQLite, manual table rebuild would be needed (see migration docs)
    if dialect == "postgresql":
        op.drop_column("proxy_token_jtis", "expired_at")
    else:
        # SQLite: Log warning, don't drop column (backward compatibility)
        logger.warning(
            "SQLite does not support DROP COLUMN in older versions. "
            "Column expired_at will remain but be ignored by older code."
        )

    logger.info("Downgrade 20260724_001 completed: expired_at removed from proxy_token_jtis")