#!/usr/bin/env python3
"""
Initialize Authentication Database

This script creates the authentication database tables and adds a default admin user.
"""

import os
import sys
import hashlib

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')

if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import db


def create_default_admin(username: str = 'admin', password: str = 'admin123',
                         email: str = 'admin@localhost') -> bool:
    """Create a default admin user."""
    # Hash password using SHA256 (for production, use bcrypt)
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    # Check if admin already exists
    existing = db.get_user_by_username(username)
    if existing:
        print(f"Admin user '{username}' already exists")
        return True

    # Create admin user
    result = db.create_user(
        username=username,
        password_hash=password_hash,
        email=email,
        role='admin',
        daily_token_quota=10000000,  # 10M tokens
        daily_request_quota=10000
    )

    if result:
        print(f"Created default admin user: {username}")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print("\nWARNING: Please change the default password immediately!")
        return True
    else:
        print(f"Failed to create admin user '{username}'")
        return False


def main():
    """Main function to initialize auth database."""
    print("Initializing Authentication Database...")

    # Initialize auth database tables
    db.init_auth_database()

    # Create default admin user
    create_default_admin()

    print("\nAuthentication database initialization complete!")
    print("\nDefault admin credentials:")
    print("  Username: admin")
    print("  Password: admin123")
    print("\nPlease change the default password after first login!")


if __name__ == '__main__':
    main()
