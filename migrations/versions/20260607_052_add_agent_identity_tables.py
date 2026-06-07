"""Add agent identity tables for remote agent token management

Revision ID: 052_agent_identity
Revises: 051_fix_project_path_unique
Create Date: 2026-06-07

This migration adds two new tables and one column to support durable
registration tokens and per-machine agent tokens, replacing the in-memory
token storage that is lost on server restart.

Tables:
- registration_tokens: Persisted one-time registration tokens with TTL
- agent_tokens: Per-machine agent credential hashes (SHA-256)

Column added:
- remote_machines.legacy_mode: Marks pre-existing agents that registered
  before agent tokens existed, allowing a graceful upgrade path.

Issue: #754
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "052_agent_identity"
down_revision: Union[str, None] = "051_fix_project_path_unique"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"
            ),
            {"name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :name"),
            {"name": table_name},
        )
        return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :col)"
            ),
            {"table": table_name, "col": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(sa.text(f"PRAGMA table_info({table_name})"))
        return any(row[1] == column_name for row in result.fetchall())


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"
    id_type = "SERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN DEFAULT FALSE" if is_pg else "INTEGER DEFAULT 0"

    # --- registration_tokens table ---
    if not _table_exists(conn, "registration_tokens"):
        op.execute(
            sa.text(
                f"""
                CREATE TABLE registration_tokens (
                    id {id_type},
                    token TEXT NOT NULL UNIQUE,
                    tenant_id INTEGER NOT NULL,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_consumed {bool_type},
                    consumed_at TIMESTAMP,
                    consumed_machine_id TEXT
                )
                """
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_registration_tokens_token "
                "ON registration_tokens(token)"
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_registration_tokens_expires "
                "ON registration_tokens(expires_at)"
            )
        )

    # --- agent_tokens table ---
    if not _table_exists(conn, "agent_tokens"):
        op.execute(
            sa.text(
                f"""
                CREATE TABLE agent_tokens (
                    id {id_type},
                    machine_id TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    rotated_at TIMESTAMP,
                    rotated_by INTEGER,
                    is_revoked {bool_type},
                    revoked_at TIMESTAMP,
                    revoked_by INTEGER
                )
                """
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_agent_tokens_machine_id "
                "ON agent_tokens(machine_id)"
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_agent_tokens_token_hash "
                "ON agent_tokens(token_hash)"
            )
        )

    # --- Add legacy_mode column to remote_machines ---
    if not _column_exists(conn, "remote_machines", "legacy_mode"):
        if is_pg:
            op.execute(
                sa.text("ALTER TABLE remote_machines ADD COLUMN legacy_mode BOOLEAN DEFAULT FALSE")
            )
        else:
            op.execute(
                sa.text("ALTER TABLE remote_machines ADD COLUMN legacy_mode INTEGER DEFAULT 0")
            )

    # Mark all existing machines as legacy (they registered before agent tokens)
    if _table_exists(conn, "remote_machines"):
        op.execute(
            sa.text(
                "UPDATE remote_machines SET legacy_mode = "
                + ("TRUE" if is_pg else "1")
                + " WHERE legacy_mode IS NULL OR legacy_mode = "
                + ("FALSE" if is_pg else "0")
            )
        )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    # Drop legacy_mode column from remote_machines
    # SQLite doesn't support DROP COLUMN easily, so we skip for SQLite
    if conn.dialect.name == "postgresql":
        if _column_exists(conn, "remote_machines", "legacy_mode"):
            op.execute(sa.text("ALTER TABLE remote_machines DROP COLUMN legacy_mode"))

    # Drop agent_tokens table
    if _table_exists(conn, "agent_tokens"):
        op.execute(sa.text("DROP TABLE agent_tokens"))

    # Drop registration_tokens table
    if _table_exists(conn, "registration_tokens"):
        op.execute(sa.text("DROP TABLE registration_tokens"))
