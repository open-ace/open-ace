#!/usr/bin/env python3
"""
Initialize Database Admin User

This script creates a default admin user and default tenant.
Database schema is created by schema.sql during installation.
"""

import os
import sys

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
            db._convert_sql("""
                INSERT INTO tenants (id, name, slug, status, plan)
                VALUES (?, ?, ?, 'active', 'standard')
            """),
            (tenant_id, name, slug),
        )

        # Create tenant_quotas
        cursor.execute(
            db._convert_sql("""
                INSERT INTO tenant_quotas (tenant_id, daily_token_limit, monthly_token_limit,
                    daily_request_limit, monthly_request_limit, max_users, max_sessions_per_user)
                VALUES (?, 10000000, 300000000, 10000, 300000, 100, 10)
            """),
            (tenant_id,),
        )

        # Create tenant_settings - use same approach for both databases
        # PostgreSQL: psycopg2 converts Python True/False to PostgreSQL TRUE/FALSE
        # SQLite: use integer 1/0 for boolean
        if db.is_postgresql():
            bool_true = True
            bool_false = False
        else:
            bool_true = 1
            bool_false = 0

        cursor.execute(
            db._convert_sql("""
                INSERT INTO tenant_settings (tenant_id, content_filter_enabled, audit_log_enabled,
                    audit_log_retention_days, data_retention_days, sso_enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            """),
            (tenant_id, bool_true, bool_true, 90, 365, bool_false),
        )

        conn.commit()
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
    system_account: str = None,
    tenant_id: int = 1,
) -> bool:
    """Create a default admin user with forced password change on first login."""
    # Hash password using bcrypt
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

    # Check if admin already exists
    existing = db.get_user_by_username(username)
    if existing:
        print(f"Admin user '{username}' already exists")
        return True

    # Create admin user with must_change_password = True (force password change on first login)
    result = db.create_user_with_is_active(
        username=username,
        password_hash=password_hash,
        email=email,
        role="admin",
        daily_token_quota=10000000,  # 10M tokens
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
        return True
    else:
        print(f"Failed to create admin user '{username}'")
        return False


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
    create_default_admin(system_account=system_account, tenant_id=1)

    print("\nDefault admin credentials:")
    print("  Username: admin")
    print("  Password: admin123")
    print("\nPlease change the default password after first login!")


if __name__ == "__main__":
    main()
