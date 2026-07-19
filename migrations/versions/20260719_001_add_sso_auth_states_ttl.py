"""add expires_at to sso_auth_states

Revision ID: 20260719_001_add_sso_auth_states_ttl
Revises: 20260718_001_add_remote_runtime_state
Create Date: 2026-07-19

Issue #1815 Finding 2: Add TTL mechanism to sso_auth_states table.

- Adds expires_at column (NOT NULL)
- Backfills existing rows with expires_at = created_at + 10min
- Creates index for efficient cleanup queries

This prevents unbounded PKCE verifier/CSRF state accumulation and limits
the authorization-code replay window.
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260719_001_add_sso_auth_states_ttl"
down_revision: Union[str, None] = "20260718_001_add_remote_runtime_state"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

# Default TTL for auth_state records (10 minutes)
DEFAULT_TTL_SECONDS = 600


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM information_schema.columns"
                "  WHERE table_name = :table_name AND column_name = :column_name"
                ")"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        # SQLite
        result = conn.execute(
            sa.text(
                f"SELECT name FROM pragma_table_info('{table_name}') WHERE name = :column_name"
            ),
            {"column_name": column_name},
        )
        return result.fetchone() is not None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM pg_indexes"
                "  WHERE tablename = :table_name AND indexname = :index_name"
                ")"
            ),
            {"table_name": table_name, "index_name": index_name},
        )
        return result.fetchone()[0]
    else:
        # SQLite
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Add expires_at column and index to sso_auth_states table.

    For fresh DBs the column is already created in the baseline migration
    (20260703_002). This migration handles existing DBs that predate the
    expires_at column.
    """
    conn = op.get_bind()

    # Check if table exists first
    if conn.dialect.name == "postgresql":
        table_exists_result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM information_schema.tables"
                "  WHERE table_name = 'sso_auth_states'"
                ")"
            )
        )
        table_exists = table_exists_result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name='sso_auth_states'")
        )
        table_exists = result.fetchone() is not None

    if not table_exists:
        log.info("sso_auth_states table does not exist, skipping")
        return

    # If column already exists (fresh DB where baseline CREATE TABLE includes it),
    # just ensure the index exists and return.
    if _column_exists(conn, "sso_auth_states", "expires_at"):
        log.info("expires_at column already exists, ensuring index")
        index_name = "idx_sso_auth_states_expires"
        if not _index_exists(conn, "sso_auth_states", index_name):
            op.execute(f"CREATE INDEX {index_name} ON sso_auth_states(expires_at)")
        return

    # Add expires_at column if not exists
    if not _column_exists(conn, "sso_auth_states", "expires_at"):
        log.info("Adding expires_at column to sso_auth_states")

        if conn.dialect.name == "postgresql":
            # PostgreSQL: Add column without default first, then backfill, then set NOT NULL
            op.execute("ALTER TABLE sso_auth_states ADD COLUMN expires_at TIMESTAMP")

            # Backfill existing rows: expires_at = created_at + 10min
            # Handle NULL created_at by using current timestamp
            op.execute(
                f"""
                UPDATE sso_auth_states
                SET expires_at = COALESCE(
                    created_at + INTERVAL '{DEFAULT_TTL_SECONDS} seconds',
                    NOW() + INTERVAL '{DEFAULT_TTL_SECONDS} seconds'
                )
                WHERE expires_at IS NULL
            """
            )

            # Set NOT NULL constraint
            op.execute("ALTER TABLE sso_auth_states ALTER COLUMN expires_at SET NOT NULL")
        else:
            # SQLite: Add column with default for new rows, backfill existing
            op.execute(
                f"""
                ALTER TABLE sso_auth_states
                ADD COLUMN expires_at TIMESTAMP NOT NULL DEFAULT
                    (datetime('now', '+{DEFAULT_TTL_SECONDS} seconds'))
            """
            )

            # Backfill existing rows using created_at
            op.execute(
                f"""
                UPDATE sso_auth_states
                SET expires_at = datetime(
                    COALESCE(created_at, datetime('now')),
                    '+{DEFAULT_TTL_SECONDS} seconds'
                )
                WHERE expires_at IS NULL OR expires_at = ''
            """
            )
    else:
        log.info("expires_at column already exists, skipping")

    # Create index for efficient cleanup queries
    index_name = "idx_sso_auth_states_expires"
    if not _index_exists(conn, "sso_auth_states", index_name):
        log.info(f"Creating index {index_name}")
        op.execute(f"CREATE INDEX {index_name} ON sso_auth_states(expires_at)")
    else:
        log.info(f"Index {index_name} already exists, skipping")


def downgrade() -> None:
    """Remove expires_at column and index from sso_auth_states table."""
    conn = op.get_bind()

    # Drop index first
    index_name = "idx_sso_auth_states_expires"
    if _index_exists(conn, "sso_auth_states", index_name):
        log.info(f"Dropping index {index_name}")
        op.execute(f"DROP INDEX {index_name}")

    # Drop column
    if _column_exists(conn, "sso_auth_states", "expires_at"):
        log.info("Dropping expires_at column from sso_auth_states")
        if conn.dialect.name == "postgresql":
            op.execute("ALTER TABLE sso_auth_states DROP COLUMN expires_at")
        else:
            # SQLite doesn't support DROP COLUMN in older versions
            # For SQLite 3.35.0+, this works
            try:
                op.execute("ALTER TABLE sso_auth_states DROP COLUMN expires_at")
            except Exception as e:
                log.warning(f"Could not drop column in SQLite: {e}")
                # Leave column in place for SQLite older versions
