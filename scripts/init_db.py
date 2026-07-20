#!/usr/bin/env python3
"""
Initialize Database Admin User

This script creates a default admin user and default tenant.
Database schema is created by schema.sql during installation.
"""

from __future__ import annotations


from __future__ import annotations


from __future__ import annotations
import os
import sys
from typing import Optional, Tuple

import bcrypt

# Add scripts directory to path for standalone script execution
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from shared import db


def create_default_tenant(
    name: str = "Default",
    slug: str = "default",
    tenant_id: int = 1,
) -> bool:
    """Create a default tenant with id=1 for remote agent registration."""
    conn = db.get_connection()
    cursor = conn.cursor()

    # Check if tenant already exists
    cursor.execute(db._convert_sql("SELECT id FROM tenants WHERE id = ?"), (tenant_id,))
    existing = cursor.fetchone()
    if existing:
        print(f"Default tenant (id={tenant_id}) already exists")
        conn.close()
        return True

    try:
        # Create tenant
        cursor.execute(
            db._convert_sql(
                """
                INSERT INTO tenants (id, name, slug, status, plan)
                VALUES (?, ?, ?, 'active', 'standard')
            """
            ),
            (tenant_id, name, slug),
        )

        # Create tenant_quotas
        cursor.execute(
            db._convert_sql(
                """
                INSERT INTO tenant_quotas (tenant_id, daily_token_limit, monthly_token_limit,
                    daily_request_limit, monthly_request_limit, max_users, max_sessions_per_user)
                VALUES (?, 10000000, 300000000, 10000, 300000, 100, 10)
            """
            ),
            (tenant_id,),
        )

        # Create tenant_settings - use same approach for both databases
        # PostgreSQL: psycopg2 converts Python True/False to PostgreSQL TRUE/FALSE
        # SQLite: use integer 1/0 for boolean
        if db.is_postgresql():
            bool_true: bool | int = True
            bool_false: bool | int = False
        else:
            bool_true = 1
            bool_false = 0

        cursor.execute(
            db._convert_sql(
                """
                INSERT INTO tenant_settings (tenant_id, content_filter_enabled, audit_log_enabled,
                    audit_log_retention_days, data_retention_days, sso_enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            ),
            (tenant_id, bool_true, bool_true, 90, 365, bool_false),
        )

        conn.commit()

        # Sync PostgreSQL sequence after inserting with explicit id
        # This prevents "duplicate key violates unique constraint" errors
        # when subsequent inserts use the sequence's default value
        if db.is_postgresql():
            cursor.execute("SELECT setval('tenants_id_seq', (SELECT MAX(id) FROM tenants))")
            conn.commit()
            print("Synced PostgreSQL sequence tenants_id_seq")

        print(f"Created default tenant: {name} (id={tenant_id})")
        conn.close()
        return True
    except Exception as e:
        print(f"Failed to create default tenant: {e}")
        conn.close()
        return False


def create_default_admin(
    username: str = "admin",
    password: str = "admin123",
    email: str = "admin@localhost",
    system_account: str | None = None,
    tenant_id: int = 1,
) -> tuple[bool, bool]:
    """Create a default admin user with forced password change on first login.

    Returns:
        Tuple[bool, bool]: (success, is_new_user)
            - success: True if operation completed successfully
            - is_new_user: True if a new user was created, False if user already existed
    """
    # Hash password using bcrypt
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

    # Check if admin already exists
    existing = db.get_user_by_username(username)
    if existing:
        # Update system_account if provided and current value is NULL or different
        # Note: Only system_account is updated; other fields (email, tenant_id) are not modified
        # as they are typically set during initial creation and should not be changed by reinstall
        if system_account and existing.get("system_account") != system_account:
            # Validate system_account parameter
            if not system_account.strip():
                print("Warning: system_account is empty string, skipping update")
                return (True, False)
            try:
                db.update_user(existing["id"], system_account=system_account)
                print(f"Updated system_account for '{username}' to '{system_account}'")
            except Exception as e:
                print(f"Warning: Failed to update system_account for '{username}': {e}")
        print(f"Admin user '{username}' already exists")
        return (True, False)

    # Create admin user with must_change_password = True (force password change on first login)
    result = db.create_user_with_is_active(
        username=username,
        password_hash=password_hash,
        email=email,
        role="admin",
        daily_token_quota=10,  # 10M tokens (stored in M units)
        daily_request_quota=10000,
        is_active=True,
        must_change_password=True,  # Force password change on first login
        system_account=system_account,
        tenant_id=tenant_id,
    )

    if result:
        print(f"Created default admin user: {username}")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print(f"Tenant ID: {tenant_id}")
        print("\nIMPORTANT: You MUST change the password on first login!")
        return (True, True)
    else:
        print(f"Failed to create admin user '{username}'")
        return (False, False)


def main():
    """Main function to create default tenant and admin user."""
    print("Initializing default tenant and admin user...")

    # Get system_account from command line argument or environment variable
    system_account = None
    if len(sys.argv) > 1:
        system_account = sys.argv[1]
    elif os.environ.get("OPENACE_SYSTEM_ACCOUNT"):
        system_account = os.environ.get("OPENACE_SYSTEM_ACCOUNT")

    # Create default tenant first (id=1)
    create_default_tenant()

    # Create default admin user with tenant_id=1
    success, is_new_user = create_default_admin(system_account=system_account, tenant_id=1)

    # Only show default password message when a new user was actually created
    if is_new_user:
        print("\nDefault admin credentials:")
        print("  Username: admin")
        print("  Password: admin123")
        print("\nPlease change the default password after first login!")
    else:
        print("\nAdmin user 'admin' already exists - password unchanged.")
        print("If you forgot the password, please use the password reset feature.")


if __name__ == "__main__":
    main()
