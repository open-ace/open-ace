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

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260709_001_add_readonly_role_to_check_constraint"
down_revision: str | None = "20260707_001_add_system_account_to_workflows"
branch_labels: str | None = None
depends_on: str | None = None


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
        op.execute("DROP INDEX IF EXISTS idx_users_deleted")

        # Get current columns to handle any schema evolution
        inspector = sa.inspect(connection)
        columns_info = inspector.get_columns("users")
        column_names = {col["name"] for col in columns_info}

        # Build column list dynamically based on current schema
        # Use all columns from the actual table to ensure consistency
        all_columns = sorted(column_names)

        # Column definitions for CREATE TABLE
        col_defs = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "username": "TEXT NOT NULL UNIQUE",
            "password_hash": "TEXT NOT NULL",
            "email": "TEXT",
            "is_admin": "INTEGER DEFAULT 0",
            "is_active": "INTEGER DEFAULT 1",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "last_login": "TIMESTAMP",
            "role": "TEXT DEFAULT 'user'",
            "daily_token_quota": "INTEGER",
            "monthly_token_quota": "INTEGER",
            "daily_request_quota": "INTEGER",
            "monthly_request_quota": "INTEGER",
            "deleted_at": "TIMESTAMP",
            "system_account": "TEXT",
            "tenant_id": "INTEGER REFERENCES tenants(id) ON DELETE SET NULL",
            "must_change_password": "INTEGER DEFAULT 0",
            "avatar_url": "TEXT",
            "auto_mapping_enabled": "INTEGER DEFAULT 1",
        }

        # Build CREATE TABLE statement dynamically
        create_parts = []
        for col_name in all_columns:
            if col_name in col_defs:
                create_parts.append(f"{col_name} {col_defs[col_name]}")
        # Add CHECK constraint with readonly role
        create_parts.append(
            "CONSTRAINT chk_users_role CHECK (role IN ('admin', 'manager', 'user', 'readonly'))"
        )
        create_sql = "CREATE TABLE users_new (\n    " + ",\n    ".join(create_parts) + "\n)"
        op.execute(create_sql)

        # Copy data from old table to new table using matching columns
        columns_str = ", ".join(all_columns)
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
        op.execute("CREATE INDEX idx_users_deleted ON users_new(deleted_at)")

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
        op.execute("DROP INDEX IF EXISTS idx_users_deleted")

        inspector = sa.inspect(connection)
        columns_info = inspector.get_columns("users")
        column_names = {col["name"] for col in columns_info}

        # Migrate 'readonly' to 'viewer' first (before table recreation)
        op.execute(
            """
            UPDATE users
            SET role = 'viewer'
            WHERE role = 'readonly'
            """
        )

        # Build column list dynamically based on current schema
        # Use all columns from the actual table to ensure consistency
        all_columns = sorted(column_names)

        # Column definitions for CREATE TABLE
        col_defs = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "username": "TEXT NOT NULL UNIQUE",
            "password_hash": "TEXT NOT NULL",
            "email": "TEXT",
            "is_admin": "INTEGER DEFAULT 0",
            "is_active": "INTEGER DEFAULT 1",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "last_login": "TIMESTAMP",
            "role": "TEXT DEFAULT 'user'",
            "daily_token_quota": "INTEGER",
            "monthly_token_quota": "INTEGER",
            "daily_request_quota": "INTEGER",
            "monthly_request_quota": "INTEGER",
            "deleted_at": "TIMESTAMP",
            "system_account": "TEXT",
            "tenant_id": "INTEGER REFERENCES tenants(id) ON DELETE SET NULL",
            "must_change_password": "INTEGER DEFAULT 0",
            "avatar_url": "TEXT",
            "auto_mapping_enabled": "INTEGER DEFAULT 1",
        }

        # Build CREATE TABLE statement dynamically
        create_parts = []
        for col_name in all_columns:
            if col_name in col_defs:
                create_parts.append(f"{col_name} {col_defs[col_name]}")
        # Add CHECK constraint without readonly role
        create_parts.append(
            "CONSTRAINT chk_users_role CHECK (role IN ('admin', 'manager', 'user'))"
        )
        create_sql = "CREATE TABLE users_new (\n    " + ",\n    ".join(create_parts) + "\n)"
        op.execute(create_sql)

        columns_str = ", ".join(all_columns)
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
        op.execute("CREATE INDEX idx_users_deleted ON users_new(deleted_at)")

        op.drop_table("users")
        op.rename_table("users_new", "users")
