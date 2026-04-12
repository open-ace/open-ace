#!/usr/bin/env python3
"""
Initialize Database Admin User

This script creates a default admin user.
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


def create_default_admin(
    username: str = "admin",
    password: str = "admin123",
    email: str = "admin@localhost",
    system_account: str = None,
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
    )

    if result:
        print(f"Created default admin user: {username}")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print("\nIMPORTANT: You MUST change the password on first login!")
        return True
    else:
        print(f"Failed to create admin user '{username}'")
        return False


def main():
    """Main function to create default admin user."""
    print("Creating default admin user...")

    # Get system_account from command line argument or environment variable
    system_account = None
    if len(sys.argv) > 1:
        system_account = sys.argv[1]
    elif os.environ.get("OPENACE_SYSTEM_ACCOUNT"):
        system_account = os.environ.get("OPENACE_SYSTEM_ACCOUNT")

    # Create default admin user
    create_default_admin(system_account=system_account)

    print("\nDefault admin credentials:")
    print("  Username: admin")
    print("  Password: admin123")
    print("\nPlease change the default password after first login!")


if __name__ == "__main__":
    main()
