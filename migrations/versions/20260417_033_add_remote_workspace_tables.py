"""Add remote workspace tables

Revision ID: 033_add_remote_workspace_tables
Revises: 032_add_insights_reports
Create Date: 2026-04-17

This migration adds tables for remote workspace support:
- remote_machines: Registered remote machines
- machine_assignments: User access to remote machines
- api_key_store: Encrypted API keys for LLM proxy

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "033_add_remote_workspace_tables"
down_revision: Union[str, None] = "032_add_insights_reports"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"
            ),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(f"PRAGMA table_info({table_name})")
        )
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
    else:
        result = conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add remote workspace tables."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    id_type = "SERIAL PRIMARY KEY" if is_postgresql else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # Create remote_machines table
    if not _table_exists(conn, "remote_machines"):
        if is_postgresql:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE remote_machines (
                        id {id_type},
                        machine_id TEXT NOT NULL UNIQUE,
                        machine_name TEXT NOT NULL,
                        hostname TEXT,
                        os_type TEXT,
                        os_version TEXT,
                        ip_address TEXT,
                        status TEXT DEFAULT 'offline',
                        agent_version TEXT,
                        capabilities TEXT,
                        cli_path TEXT,
                        work_dir TEXT,
                        tenant_id INTEGER REFERENCES tenants(id),
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_heartbeat TIMESTAMP
                    )
                    """
                )
            )
        else:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE remote_machines (
                        id {id_type},
                        machine_id TEXT NOT NULL UNIQUE,
                        machine_name TEXT NOT NULL,
                        hostname TEXT,
                        os_type TEXT,
                        os_version TEXT,
                        ip_address TEXT,
                        status TEXT DEFAULT 'offline',
                        agent_version TEXT,
                        capabilities TEXT,
                        cli_path TEXT,
                        work_dir TEXT,
                        tenant_id INTEGER,
                        created_by INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_heartbeat TIMESTAMP
                    )
                    """
                )
            )

    if not _index_exists(conn, "remote_machines", "idx_remote_machines_machine_id"):
        op.execute(
            sa.text("CREATE INDEX idx_remote_machines_machine_id ON remote_machines(machine_id)")
        )
    if not _index_exists(conn, "remote_machines", "idx_remote_machines_status"):
        op.execute(
            sa.text("CREATE INDEX idx_remote_machines_status ON remote_machines(status)")
        )

    # Create machine_assignments table
    if not _table_exists(conn, "machine_assignments"):
        if is_postgresql:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE machine_assignments (
                        id {id_type},
                        machine_id TEXT NOT NULL REFERENCES remote_machines(machine_id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        permission TEXT DEFAULT 'user',
                        granted_by INTEGER REFERENCES users(id),
                        granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(machine_id, user_id)
                    )
                    """
                )
            )
        else:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE machine_assignments (
                        id {id_type},
                        machine_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        permission TEXT DEFAULT 'user',
                        granted_by INTEGER,
                        granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(machine_id, user_id)
                    )
                    """
                )
            )

    if not _index_exists(conn, "machine_assignments", "idx_machine_assignments_user_id"):
        op.execute(
            sa.text("CREATE INDEX idx_machine_assignments_user_id ON machine_assignments(user_id)")
        )

    # Create api_key_store table
    if not _table_exists(conn, "api_key_store"):
        if is_postgresql:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE api_key_store (
                        id {id_type},
                        tenant_id INTEGER REFERENCES tenants(id),
                        provider TEXT NOT NULL,
                        key_name TEXT NOT NULL,
                        encrypted_key TEXT NOT NULL,
                        key_hash TEXT NOT NULL,
                        base_url TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(tenant_id, provider, key_name)
                    )
                    """
                )
            )
        else:
            op.execute(
                sa.text(
                    f"""
                    CREATE TABLE api_key_store (
                        id {id_type},
                        tenant_id INTEGER,
                        provider TEXT NOT NULL,
                        key_name TEXT NOT NULL,
                        encrypted_key TEXT NOT NULL,
                        key_hash TEXT NOT NULL,
                        base_url TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_by INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(tenant_id, provider, key_name)
                    )
                    """
                )
            )

    if not _index_exists(conn, "api_key_store", "idx_api_key_store_tenant_provider"):
        op.execute(
            sa.text(
                "CREATE INDEX idx_api_key_store_tenant_provider ON api_key_store(tenant_id, provider)"
            )
        )

    # Add workspace_type and remote_machine_id columns to agent_sessions
    if not _column_exists(conn, "agent_sessions", "workspace_type"):
        op.execute(
            sa.text(
                "ALTER TABLE agent_sessions ADD COLUMN workspace_type TEXT DEFAULT 'local'"
            )
        )

    if not _column_exists(conn, "agent_sessions", "remote_machine_id"):
        op.execute(
            sa.text(
                "ALTER TABLE agent_sessions ADD COLUMN remote_machine_id TEXT"
            )
        )


def downgrade() -> None:
    """Remove remote workspace tables."""
    conn = op.get_bind()

    # Drop columns from agent_sessions (SQLite doesn't support DROP COLUMN easily,
    # so we only do this for PostgreSQL)
    if conn.dialect.name == "postgresql":
        if _column_exists(conn, "agent_sessions", "remote_machine_id"):
            op.execute(sa.text("ALTER TABLE agent_sessions DROP COLUMN remote_machine_id"))
        if _column_exists(conn, "agent_sessions", "workspace_type"):
            op.execute(sa.text("ALTER TABLE agent_sessions DROP COLUMN workspace_type"))

    # Drop tables
    for table in ["machine_assignments", "api_key_store", "remote_machines"]:
        if _table_exists(conn, table):
            op.execute(sa.text(f"DROP TABLE {table}"))
