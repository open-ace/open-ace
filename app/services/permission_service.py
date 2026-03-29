#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Permission Service

Business logic for role-based access control (RBAC).
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from app.repositories.database import Database
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class Permission(Enum):
    """System permissions."""

    # Dashboard permissions
    VIEW_DASHBOARD = "view_dashboard"

    # Messages permissions
    VIEW_MESSAGES = "view_messages"
    EXPORT_MESSAGES = "export_messages"

    # Analysis permissions
    VIEW_ANALYSIS = "view_analysis"
    RUN_ANALYSIS = "run_analysis"
    EXPORT_ANALYSIS = "export_analysis"

    # User management permissions
    VIEW_USERS = "view_users"
    CREATE_USER = "create_user"
    EDIT_USER = "edit_user"
    DELETE_USER = "delete_user"

    # Permission management
    MANAGE_PERMISSIONS = "manage_permissions"

    # Quota management
    VIEW_QUOTA = "view_quota"
    MANAGE_QUOTA = "manage_quota"

    # Audit logs
    VIEW_AUDIT_LOGS = "view_audit_logs"
    EXPORT_AUDIT_LOGS = "export_audit_logs"

    # Content filter
    VIEW_CONTENT_FILTER = "view_content_filter"
    MANAGE_CONTENT_FILTER = "manage_content_filter"

    # System administration
    ADMIN_ACCESS = "admin_access"
    SYSTEM_CONFIG = "system_config"


@dataclass
class Role:
    """Role definition."""

    name: str
    description: str
    permissions: Set[str] = field(default_factory=set)

    def has_permission(self, permission: str) -> bool:
        """Check if role has a permission."""
        return permission in self.permissions or Permission.ADMIN_ACCESS.value in self.permissions


# Default role definitions
DEFAULT_ROLES = {
    "admin": Role(
        name="admin",
        description="Full system administrator with all permissions",
        permissions={p.value for p in Permission},
    ),
    "manager": Role(
        name="manager",
        description="Team manager with view and export permissions",
        permissions={
            Permission.VIEW_DASHBOARD.value,
            Permission.VIEW_MESSAGES.value,
            Permission.EXPORT_MESSAGES.value,
            Permission.VIEW_ANALYSIS.value,
            Permission.RUN_ANALYSIS.value,
            Permission.EXPORT_ANALYSIS.value,
            Permission.VIEW_USERS.value,
            Permission.VIEW_QUOTA.value,
            Permission.VIEW_AUDIT_LOGS.value,
            Permission.EXPORT_AUDIT_LOGS.value,
            Permission.VIEW_CONTENT_FILTER.value,
        },
    ),
    "user": Role(
        name="user",
        description="Regular user with basic view permissions",
        permissions={
            Permission.VIEW_DASHBOARD.value,
            Permission.VIEW_MESSAGES.value,
            Permission.VIEW_ANALYSIS.value,
            Permission.VIEW_QUOTA.value,
        },
    ),
    "readonly": Role(
        name="readonly",
        description="Read-only access to dashboard",
        permissions={
            Permission.VIEW_DASHBOARD.value,
        },
    ),
}


class PermissionService:
    """
    Permission management service for RBAC.

    Features:
    - Role-based access control
    - Custom permissions per user
    - Permission checking and validation
    """

    def __init__(self, db: Optional[Database] = None, user_repo: Optional[UserRepository] = None):
        """
        Initialize permission service.

        Args:
            db: Optional Database instance.
            user_repo: Optional UserRepository instance.
        """
        self.db = db or Database()
        self.user_repo = user_repo or UserRepository()
        self.roles = dict(DEFAULT_ROLES)
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure permission tables exist."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
            id_type = (
                "SERIAL PRIMARY KEY"
                if self.db.is_postgresql
                else "INTEGER PRIMARY KEY AUTOINCREMENT"
            )

            # User permissions table (for custom permissions)
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS user_permissions (
                    id {id_type},
                    user_id INTEGER NOT NULL,
                    permission TEXT NOT NULL,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    granted_by INTEGER,
                    UNIQUE(user_id, permission),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """
            )

            # Role permissions override table
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS role_permissions (
                    id {id_type},
                    role_name TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(role_name, permission)
                )
            """
            )

            # Create indexes
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions(user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions(role_name)"
            )

            conn.commit()

            # Load custom role permissions
            self._load_role_permissions()

    def _load_role_permissions(self) -> None:
        """Load custom role permissions from database."""
        rows = self.db.fetch_all("SELECT role_name, permission FROM role_permissions")

        for row in rows:
            role_name = row.get("role_name")
            permission = row.get("permission")

            if role_name and permission:
                if role_name not in self.roles:
                    self.roles[role_name] = Role(
                        name=role_name, description=f"Custom role: {role_name}", permissions=set()
                    )
                self.roles[role_name].permissions.add(permission)

    def get_role(self, role_name: str) -> Optional[Role]:
        """
        Get role definition.

        Args:
            role_name: Name of the role.

        Returns:
            Optional[Role]: Role definition or None.
        """
        return self.roles.get(role_name)

    def get_all_roles(self) -> Dict[str, Role]:
        """Get all role definitions."""
        return self.roles

    def get_user_permissions(self, user_id: int) -> Set[str]:
        """
        Get all permissions for a user.

        Combines role permissions with custom user permissions.

        Args:
            user_id: User ID.

        Returns:
            Set[str]: Set of permission strings.
        """
        # Get user's role
        user = self.user_repo.get_user_by_id(user_id)
        if not user:
            return set()

        role_name = user.get("role", "user")
        role = self.roles.get(role_name, self.roles.get("user"))

        # Start with role permissions
        permissions = set(role.permissions) if role else set()

        # Add custom user permissions
        custom_perms = self.db.fetch_all(
            "SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)
        )

        for perm in custom_perms:
            permissions.add(perm.get("permission"))

        return permissions

    def has_permission(self, user_id: int, permission: str) -> bool:
        """
        Check if user has a specific permission.

        Args:
            user_id: User ID.
            permission: Permission to check.

        Returns:
            bool: True if user has permission.
        """
        permissions = self.get_user_permissions(user_id)

        # Admin access grants all permissions
        if Permission.ADMIN_ACCESS.value in permissions:
            return True

        return permission in permissions

    def check_permission(self, user_id: int, permission: str) -> tuple:
        """
        Check permission and return result with error message.

        Args:
            user_id: User ID.
            permission: Permission to check.

        Returns:
            tuple: (has_permission, error_message or None)
        """
        if self.has_permission(user_id, permission):
            return True, None

        return False, f"Permission denied: {permission}"

    def grant_permission(self, user_id: int, permission: str, granted_by: int) -> bool:
        """
        Grant a permission to a user.

        Args:
            user_id: User to grant permission to.
            permission: Permission to grant.
            granted_by: ID of user granting the permission.

        Returns:
            bool: True if successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO user_permissions
                    (user_id, permission, granted_by)
                    VALUES (?, ?, ?)
                """,
                    (user_id, permission, granted_by),
                )
                conn.commit()

            logger.info(f"Permission '{permission}' granted to user {user_id} by {granted_by}")
            return True

        except Exception as e:
            logger.error(f"Failed to grant permission: {e}")
            return False

    def revoke_permission(self, user_id: int, permission: str) -> bool:
        """
        Revoke a permission from a user.

        Args:
            user_id: User to revoke permission from.
            permission: Permission to revoke.

        Returns:
            bool: True if successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM user_permissions
                    WHERE user_id = ? AND permission = ?
                """,
                    (user_id, permission),
                )
                conn.commit()

            logger.info(f"Permission '{permission}' revoked from user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to revoke permission: {e}")
            return False

    def create_role(self, role_name: str, description: str, permissions: Set[str]) -> bool:
        """
        Create a new role.

        Args:
            role_name: Name for the new role.
            description: Role description.
            permissions: Set of permissions for the role.

        Returns:
            bool: True if successful.
        """
        if role_name in self.roles:
            logger.warning(f"Role '{role_name}' already exists")
            return False

        try:
            # Add to database
            with self.db.connection() as conn:
                cursor = conn.cursor()
                for perm in permissions:
                    cursor.execute(
                        """
                        INSERT INTO role_permissions (role_name, permission)
                        VALUES (?, ?)
                    """,
                        (role_name, perm),
                    )
                conn.commit()

            # Add to memory
            self.roles[role_name] = Role(
                name=role_name, description=description, permissions=permissions
            )

            logger.info(f"Role '{role_name}' created with {len(permissions)} permissions")
            return True

        except Exception as e:
            logger.error(f"Failed to create role: {e}")
            return False

    def update_role_permissions(self, role_name: str, permissions: Set[str]) -> bool:
        """
        Update permissions for a role.

        Args:
            role_name: Role to update.
            permissions: New set of permissions.

        Returns:
            bool: True if successful.
        """
        if role_name not in self.roles:
            logger.warning(f"Role '{role_name}' does not exist")
            return False

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Remove old permissions
                cursor.execute("DELETE FROM role_permissions WHERE role_name = ?", (role_name,))

                # Add new permissions
                for perm in permissions:
                    cursor.execute(
                        """
                        INSERT INTO role_permissions (role_name, permission)
                        VALUES (?, ?)
                    """,
                        (role_name, perm),
                    )

                conn.commit()

            # Update in memory
            self.roles[role_name].permissions = permissions

            logger.info(f"Role '{role_name}' updated with {len(permissions)} permissions")
            return True

        except Exception as e:
            logger.error(f"Failed to update role: {e}")
            return False

    def delete_role(self, role_name: str) -> bool:
        """
        Delete a custom role.

        Args:
            role_name: Role to delete.

        Returns:
            bool: True if successful.
        """
        # Don't allow deleting default roles
        if role_name in DEFAULT_ROLES:
            logger.warning(f"Cannot delete default role '{role_name}'")
            return False

        if role_name not in self.roles:
            return True

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM role_permissions WHERE role_name = ?", (role_name,))
                conn.commit()

            del self.roles[role_name]

            logger.info(f"Role '{role_name}' deleted")
            return True

        except Exception as e:
            logger.error(f"Failed to delete role: {e}")
            return False

    def get_users_with_permission(self, permission: str) -> List[Dict]:
        """
        Get all users who have a specific permission.

        Args:
            permission: Permission to check.

        Returns:
            List[Dict]: List of user records.
        """
        users = self.user_repo.get_all_users(include_inactive=False)
        result = []

        for user in users:
            user_id = user.get("id")
            if user_id and self.has_permission(user_id, permission):
                result.append(user)

        return result

    def get_permission_audit_log(self, user_id: Optional[int] = None) -> List[Dict]:
        """
        Get audit log of permission changes.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            List[Dict]: List of permission changes.
        """
        if user_id:
            return self.db.fetch_all(
                """
                SELECT up.*, u.username as granted_by_username
                FROM user_permissions up
                LEFT JOIN users u ON up.granted_by = u.id
                WHERE up.user_id = ?
                ORDER BY up.granted_at DESC
            """,
                (user_id,),
            )
        else:
            return self.db.fetch_all(
                """
                SELECT up.*, u.username, grantor.username as granted_by_username
                FROM user_permissions up
                JOIN users u ON up.user_id = u.id
                LEFT JOIN users grantor ON up.granted_by = grantor.id
                ORDER BY up.granted_at DESC
                LIMIT 100
            """
            )
