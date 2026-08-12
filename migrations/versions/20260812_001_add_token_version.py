"""Add token_version field to agent_tokens table

Issue #2530: Token rotation version tracking for preventing rollback.

This migration adds:
- token_version column (BIGINT, default 0)
- Index on (machine_id, token_version) for efficient queries
- Trigger to auto-set version on insert during migration period

Revision ID: 20260812_001
Revises: 20260810_002_add_scheduler_lock_tracking
Create Date: 2026-08-12

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260812_001"
down_revision = "20260810_002_add_scheduler_lock_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add token_version column and related infrastructure."""
    # Get database type for conditional logic
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add token_version column with default value 0
    if dialect == "sqlite":
        # SQLite: Check and add column
        inspector = sa.inspect(bind)
        columns = [col["name"] for col in inspector.get_columns("agent_tokens")]

        if "token_version" not in columns:
            op.add_column(
                "agent_tokens",
                sa.Column(
                    "token_version",
                    sa.BigInteger(),
                    nullable=False,
                    server_default="0",
                ),
            )
    else:
        # PostgreSQL
        op.add_column(
            "agent_tokens",
            sa.Column(
                "token_version",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )

    # Create index on (machine_id, token_version) for efficient version queries
    op.create_index(
        "idx_agent_tokens_machine_version",
        "agent_tokens",
        ["machine_id", "token_version"],
        unique=False,
    )

    # Create trigger to auto-set token_version during migration period
    # This ensures new tokens created during migration get correct version numbers
    if dialect == "postgresql":
        # PostgreSQL: Use plpgsql trigger
        op.execute("""
            CREATE OR REPLACE FUNCTION set_token_version_trigger()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.token_version = 0 THEN
                    NEW.token_version := COALESCE(
                        (SELECT MAX(token_version) + 1 FROM agent_tokens WHERE machine_id = NEW.machine_id),
                        1
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)

        op.execute("""
            CREATE TRIGGER trigger_set_token_version
            BEFORE INSERT ON agent_tokens
            FOR EACH ROW
            EXECUTE FUNCTION set_token_version_trigger();
        """)


def downgrade() -> None:
    """Remove token_version column and related infrastructure."""
    # Get database type
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Drop trigger first (PostgreSQL)
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trigger_set_token_version ON agent_tokens;")
        op.execute("DROP FUNCTION IF EXISTS set_token_version_trigger();")

    # Drop index
    op.drop_index("idx_agent_tokens_machine_version", table_name="agent_tokens")

    # Drop column (PostgreSQL only; SQLite doesn't support DROP COLUMN)
    if dialect != "sqlite":
        op.drop_column("agent_tokens", "token_version")
