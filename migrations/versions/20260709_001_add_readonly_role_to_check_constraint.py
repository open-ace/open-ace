"""Add readonly role to users CHECK constraint

Revision ID: 20260709_001_add_readonly_role_to_check_constraint
Revises: 20260707_001_add_system_account_to_workflows
Create Date: 2026-07-09

Issue: #1497
The frontend defined 'viewer' role but the permission system uses 'readonly'.
The database CHECK constraint only allowed 'admin', 'manager', 'user'.
This migration:
1. Migrates any existing 'viewer' users to 'readonly' (if any)
2. Updates the CHECK constraint to include 'readonly'
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260709_001_add_readonly_role_to_check_constraint"
down_revision: Union[str, None] = "20260707_001_add_system_account_to_workflows"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    is_postgresql = dialect == "postgresql"

    # Step 1: Migrate any existing 'viewer' users to 'readonly'
    # This handles compatibility for any users that might have been created with 'viewer'
    if is_postgresql:
        op.execute(
            """
            UPDATE users
            SET role = 'readonly'
            WHERE role = 'viewer'
            """
        )
    else:  # SQLite
        op.execute(
            """
            UPDATE users
            SET role = 'readonly'
            WHERE role = 'viewer'
            """
        )

    # Step 2: Update CHECK constraint to include 'readonly'
    if is_postgresql:
        # PostgreSQL: Drop old constraint and add new one
        op.execute(
            """
            ALTER TABLE users
            DROP CONSTRAINT IF EXISTS chk_users_role
            """
        )
        op.execute(
            """
            ALTER TABLE users
            ADD CONSTRAINT chk_users_role
            CHECK (role IN ('admin', 'manager', 'user', 'readonly'))
            """
        )
    else:
        # SQLite: Need to recreate the table to update CHECK constraint
        # Drop indexes first to avoid name conflicts (SQLite indexes are globally unique)
        op.execute("DROP INDEX IF EXISTS idx_users_role")
        op.execute("DROP INDEX IF EXISTS idx_users_email")
        op.execute("DROP INDEX IF EXISTS idx_users_active")
        op.execute("DROP INDEX IF EXISTS idx_users_tenant")

        # Get current columns to handle any schema evolution
        inspector = sa.inspect(connection)
        columns_info = inspector.get_columns("users")
        column_names = {col["name"] for col in columns_info}

        # Build column list dynamically based on current schema
        # Core columns that should always exist
        core_columns = [
            "id",
            "username",
            "password_hash",
            "email",
            "role",
            "is_active",
            "created_at",
            "last_login",
            "daily_token_quota",
            "monthly_token_quota",
            "daily_request_quota",
            "monthly_request_quota",
            "tenant_id",
        ]

        # Additional columns that may exist (added after initial schema)
        optional_columns = ["system_account", "avatar_url", "must_change_password"]
        for col in optional_columns:
            if col in column_names:
                core_columns.append(col)

        # Create new table with updated CHECK constraint
        # Build CREATE TABLE statement dynamically
        create_sql = """
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'manager', 'user', 'readonly')),
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                daily_token_quota INTEGER,
                monthly_token_quota INTEGER,
                daily_request_quota INTEGER,
                monthly_request_quota INTEGER,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL
            )
        """
        op.execute(create_sql)

        # Copy data from old table to new table
        columns_str = ", ".join(core_columns)
        op.execute(
            f"""
            INSERT INTO users_new ({columns_str})
            SELECT {columns_str} FROM users
            """
        )

        # Recreate indexes
        op.execute("CREATE INDEX idx_users_role ON users_new(role)")
        op.execute("CREATE INDEX idx_users_email ON users_new(email)")
        op.execute("CREATE INDEX idx_users_active ON users_new(is_active)")
        op.execute("CREATE INDEX idx_users_tenant ON users_new(tenant_id)")

        # Drop old table and rename
        op.drop_table("users")
        op.rename_table("users_new", "users")


def downgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    is_postgresql = dialect == "postgresql"

    # Step 1: Migrate 'readonly' back to 'viewer' for downgrade consistency
    if is_postgresql:
        op.execute(
            """
            UPDATE users
            SET role = 'viewer'
            WHERE role = 'readonly'
            """
        )
        # Drop new constraint and restore old one
        op.execute(
            """
            ALTER TABLE users
            DROP CONSTRAINT IF EXISTS chk_users_role
            """
        )
        op.execute(
            """
            ALTER TABLE users
            ADD CONSTRAINT chk_users_role
            CHECK (role IN ('admin', 'manager', 'user'))
            """
        )
    else:
        # SQLite: Recreate table with old CHECK constraint
        op.execute("DROP INDEX IF EXISTS idx_users_role")
        op.execute("DROP INDEX IF EXISTS idx_users_email")
        op.execute("DROP INDEX IF EXISTS idx_users_active")
        op.execute("DROP INDEX IF EXISTS idx_users_tenant")

        inspector = sa.inspect(connection)
        columns_info = inspector.get_columns("users")
        column_names = {col["name"] for col in columns_info}

        core_columns = [
            "id",
            "username",
            "password_hash",
            "email",
            "role",
            "is_active",
            "created_at",
            "last_login",
            "daily_token_quota",
            "monthly_token_quota",
            "daily_request_quota",
            "monthly_request_quota",
            "tenant_id",
        ]
        optional_columns = ["system_account", "avatar_url", "must_change_password"]
        for col in optional_columns:
            if col in column_names:
                core_columns.append(col)

        # Migrate 'readonly' to 'viewer' first (before table recreation)
        op.execute(
            """
            UPDATE users
            SET role = 'viewer'
            WHERE role = 'readonly'
            """
        )

        create_sql = """
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'manager', 'user')),
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                daily_token_quota INTEGER,
                monthly_token_quota INTEGER,
                daily_request_quota INTEGER,
                monthly_request_quota INTEGER,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL
            )
        """
        op.execute(create_sql)

        columns_str = ", ".join(core_columns)
        op.execute(
            f"""
            INSERT INTO users_new ({columns_str})
            SELECT {columns_str} FROM users
            """
        )

        op.execute("CREATE INDEX idx_users_role ON users_new(role)")
        op.execute("CREATE INDEX idx_users_email ON users_new(email)")
        op.execute("CREATE INDEX idx_users_active ON users_new(is_active)")
        op.execute("CREATE INDEX idx_users_tenant ON users_new(tenant_id)")

        op.drop_table("users")
        op.rename_table("users_new", "users")

