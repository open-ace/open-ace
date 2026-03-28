"""Add must_change_password field to users table

Revision ID: 015_add_must_change_password
Revises: 014_optimize_msg_indexes
Create Date: 2026-03-28

This migration adds must_change_password field to users table:
- Forces users to change password on first login
- Used for default admin account security

"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '015_add_must_change_password'
down_revision: Union[str, None] = '014_optimize_msg_indexes'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    if conn.dialect.name == 'postgresql':
        result = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table_name AND column_name = :column_name"
        ), {'table_name': table_name, 'column_name': column_name})
    else:
        # SQLite
        result = conn.execute(sa.text(
            "SELECT 1 FROM pragma_table_info(:table_name) "
            "WHERE name = :column_name"
        ), {'table_name': table_name, 'column_name': column_name})
    return result.fetchone() is not None


def upgrade() -> None:
    """Add must_change_password column to users table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    if not _column_exists(conn, 'users', 'must_change_password'):
        if is_postgresql:
            op.execute("""
                ALTER TABLE users
                ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE
            """)
        else:
            # SQLite uses BOOLEAN type (stored as INTEGER 0/1)
            op.add_column('users', sa.Column(
                'must_change_password',
                sa.Boolean(),
                server_default=sa.false()
            ))

        # Set must_change_password=TRUE for existing admin users with default password
        # This ensures existing admin accounts also require password change
        op.execute("""
            UPDATE users
            SET must_change_password = TRUE
            WHERE role = 'admin' AND password_hash IS NOT NULL
        """)


def downgrade() -> None:
    """Remove must_change_password column from users table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    if _column_exists(conn, 'users', 'must_change_password'):
        if is_postgresql:
            op.execute("ALTER TABLE users DROP COLUMN must_change_password")
        else:
            # SQLite requires recreating the table without the column
            op.execute("""
                CREATE TABLE users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    daily_token_quota INTEGER DEFAULT 1000000,
                    monthly_token_quota INTEGER DEFAULT 30000000,
                    daily_request_quota INTEGER DEFAULT 1000,
                    monthly_request_quota INTEGER DEFAULT 30000,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    deleted_at TIMESTAMP,
                    tenant_id INTEGER,
                    linux_account TEXT
                )
            """)
            op.execute("""
                INSERT INTO users_new (
                    id, username, password_hash, email, role,
                    daily_token_quota, monthly_token_quota,
                    daily_request_quota, monthly_request_quota,
                    is_active, created_at, updated_at, last_login,
                    deleted_at, tenant_id, linux_account
                )
                SELECT 
                    id, username, password_hash, email, role,
                    daily_token_quota, monthly_token_quota,
                    daily_request_quota, monthly_request_quota,
                    is_active, created_at, updated_at, last_login,
                    deleted_at, tenant_id, linux_account
                FROM users
            """)
            op.execute("DROP TABLE users")
            op.execute("ALTER TABLE users_new RENAME TO users")

            # Recreate indexes
            op.create_index('idx_users_username', 'users', ['username'])
            op.create_index('idx_users_email', 'users', ['email'])
            op.create_index('idx_users_role', 'users', ['role'])
            op.create_index('idx_users_active', 'users', ['is_active'])
            op.create_index('idx_users_tenant', 'users', ['tenant_id'])