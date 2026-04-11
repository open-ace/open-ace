#!/usr/bin/env python3
"""
Open ACE - User Repository

Repository for user data access operations.
"""

import logging
from datetime import datetime
from typing import Optional

from app.repositories.database import Database, adapt_boolean_value, adapt_sql

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user data operations."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        role: str = "user",
        is_active: bool = True,
    ) -> Optional[int]:
        """
        Create a new user.

        Args:
            username: Username.
            email: Email address.
            password_hash: Hashed password.
            role: User role.
            is_active: Whether user is active.

        Returns:
            Optional[int]: User ID if successful, None otherwise.
        """
        try:
            # Use RETURNING for PostgreSQL, or lastrowid for SQLite
            if self.db.is_postgresql:
                # PostgreSQL uses TRUE/FALSE for boolean columns
                result = self.db.fetch_one(
                    """
                    INSERT INTO users (username, email, password_hash, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                """,
                    (username, email, password_hash, role, is_active, datetime.utcnow()),
                    commit=True,
                )
                return result["id"] if result else None
            else:
                # SQLite uses 1/0 for boolean columns
                is_active_int = 1 if is_active else 0
                cursor = self.db.execute(
                    """
                    INSERT INTO users (username, email, password_hash, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (username, email, password_hash, role, is_active_int, datetime.utcnow()),
                )
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        """
        Get user by ID.

        Args:
            user_id: User ID.

        Returns:
            Optional[Dict]: User data or None.
        """
        query = "SELECT * FROM users WHERE id = ?"
        return self.db.fetch_one(query, (user_id,))

    def get_user_by_username(self, username: str) -> Optional[dict]:
        """
        Get user by username.

        Args:
            username: Username.

        Returns:
            Optional[Dict]: User data or None.
        """
        query = "SELECT * FROM users WHERE username = ?"
        return self.db.fetch_one(query, (username,))

    def get_user_by_email(self, email: str) -> Optional[dict]:
        """
        Get user by email.

        Args:
            email: Email address.

        Returns:
            Optional[Dict]: User data or None.
        """
        query = "SELECT * FROM users WHERE email = ?"
        return self.db.fetch_one(query, (email,))

    def get_all_users(
        self, include_inactive: bool = True, include_deleted: bool = False
    ) -> list[dict]:
        """
        Get all users.

        Args:
            include_inactive: Whether to include inactive users.
            include_deleted: Whether to include soft-deleted users.

        Returns:
            List[Dict]: List of user records.
        """
        conditions = []
        if not include_inactive:
            conditions.append("is_active IS TRUE")
        if not include_deleted:
            conditions.append("deleted_at IS NULL")

        if conditions:
            query = f"SELECT * FROM users WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
        else:
            query = "SELECT * FROM users ORDER BY created_at DESC"

        return self.db.fetch_all(query)

    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        system_account: Optional[str] = None,
    ) -> bool:
        """
        Update user information.

        Args:
            user_id: User ID.
            username: New username.
            email: New email.
            role: New role.
            is_active: New active status.
            system_account: System account name.

        Returns:
            bool: True if successful.
        """
        updates = []
        params = []

        if username is not None:
            updates.append("username = ?")
            params.append(username)

        if email is not None:
            updates.append("email = ?")
            params.append(email)

        if role is not None:
            updates.append("role = ?")
            params.append(role)

        if is_active is not None:
            updates.append("is_active = ?")
            # PostgreSQL uses TRUE/FALSE, SQLite uses 1/0
            if self.db.is_postgresql:
                params.append(is_active)
            else:
                params.append(1 if is_active else 0)

        if system_account is not None:
            updates.append("system_account = ?")
            params.append(system_account)

        if not updates:
            return False

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"

        try:
            cursor = self.db.execute(query, tuple(params))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            return False

    def update_password(self, user_id: int, password_hash: str) -> bool:
        """
        Update user password.

        Args:
            user_id: User ID.
            password_hash: New hashed password.

        Returns:
            bool: True if successful.
        """
        query = adapt_sql(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?"
        )
        must_change_val = adapt_boolean_value(False)

        try:
            cursor = self.db.execute(query, (password_hash, must_change_val, user_id))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating password: {e}")
            return False

    def set_must_change_password(self, user_id: int, must_change: bool) -> bool:
        """
        Set must_change_password flag for a user.

        Args:
            user_id: User ID.
            must_change: Whether user must change password.

        Returns:
            bool: True if successful.
        """
        query = adapt_sql("UPDATE users SET must_change_password = ? WHERE id = ?")
        must_change_val = adapt_boolean_value(must_change)

        try:
            cursor = self.db.execute(query, (must_change_val, user_id))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error setting must_change_password: {e}")
            return False

    def update_last_login(self, user_id: int) -> bool:
        """
        Update user's last login time.

        Args:
            user_id: User ID.

        Returns:
            bool: True if successful.
        """
        query = "UPDATE users SET last_login = ? WHERE id = ?"

        try:
            self.db.execute(query, (datetime.utcnow(), user_id))
            return True
        except Exception as e:
            logger.error(f"Error updating last login: {e}")
            return False

    def delete_user(self, user_id: int, hard: bool = False) -> bool:
        """
        Delete a user (soft delete by default).

        Args:
            user_id: User ID.
            hard: If True, permanently delete; otherwise soft delete.

        Returns:
            bool: True if successful.
        """
        if hard:
            return self.hard_delete_user(user_id)

        # Soft delete - set deleted_at timestamp
        query = "UPDATE users SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL"

        try:
            cursor = self.db.execute(query, (datetime.utcnow(), user_id))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error soft deleting user: {e}")
            return False

    def restore_user(self, user_id: int) -> bool:
        """
        Restore a soft-deleted user.

        Args:
            user_id: User ID.

        Returns:
            bool: True if successful.
        """
        query = "UPDATE users SET deleted_at = NULL WHERE id = ?"

        try:
            cursor = self.db.execute(query, (user_id,))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error restoring user: {e}")
            return False

    def hard_delete_user(self, user_id: int) -> bool:
        """
        Permanently delete a user.

        Args:
            user_id: User ID.

        Returns:
            bool: True if successful.
        """
        query = "DELETE FROM users WHERE id = ?"

        try:
            cursor = self.db.execute(query, (user_id,))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error hard deleting user: {e}")
            return False

    def update_user_quota(
        self,
        user_id: int,
        daily_token_quota: Optional[int] = None,
        monthly_token_quota: Optional[int] = None,
        daily_request_quota: Optional[int] = None,
        monthly_request_quota: Optional[int] = None,
    ) -> bool:
        """
        Update user quota settings.

        Args:
            user_id: User ID.
            daily_token_quota: Daily token limit.
            monthly_token_quota: Monthly token limit.
            daily_request_quota: Daily request limit.
            monthly_request_quota: Monthly request limit.

        Returns:
            bool: True if successful.
        """
        updates = []
        params = []

        if daily_token_quota is not None:
            updates.append("daily_token_quota = ?")
            params.append(daily_token_quota)

        if monthly_token_quota is not None:
            updates.append("monthly_token_quota = ?")
            params.append(monthly_token_quota)

        if daily_request_quota is not None:
            updates.append("daily_request_quota = ?")
            params.append(daily_request_quota)

        if monthly_request_quota is not None:
            updates.append("monthly_request_quota = ?")
            params.append(monthly_request_quota)

        if not updates:
            return False

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"

        try:
            cursor = self.db.execute(query, tuple(params))
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating user quota: {e}")
            return False

    # Session management methods

    def create_session(self, user_id: int, token: str, expires_at: datetime) -> bool:
        """
        Create a new session.

        Args:
            user_id: User ID.
            token: Session token.
            expires_at: Expiration time.

        Returns:
            bool: True if successful.
        """
        query = """
            INSERT INTO sessions (user_id, token, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """

        try:
            self.db.execute(query, (user_id, token, datetime.utcnow(), expires_at))
            return True
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return False

    def get_session_by_token(self, token: str) -> Optional[dict]:
        """
        Get session by token with user information.

        Args:
            token: Session token.

        Returns:
            Optional[Dict]: Session data with user info or None.
        """
        query = """
            SELECT s.*, u.username, u.email, u.role
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > ?
        """

        return self.db.fetch_one(query, (token, datetime.utcnow()))

    def delete_session(self, token: str) -> bool:
        """
        Delete a session.

        Args:
            token: Session token.

        Returns:
            bool: True if successful.
        """
        query = "DELETE FROM sessions WHERE token = ?"

        try:
            self.db.execute(query, (token,))
            return True
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False

    def cleanup_expired_sessions(self) -> int:
        """
        Delete expired sessions.

        Returns:
            int: Number of sessions deleted.
        """
        query = "DELETE FROM sessions WHERE expires_at < ?"

        try:
            cursor = self.db.execute(query, (datetime.utcnow(),))
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}")
            return 0
