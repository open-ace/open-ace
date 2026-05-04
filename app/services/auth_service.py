"""
Open ACE - AI Computing Explorer - Auth Service

Business logic for authentication and authorization.
"""

import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Cache for security_settings queries (60 second TTL)
_security_settings_cache: dict = {}  # {"settings": dict, "timestamp": float}
_SECURITY_SETTINGS_TTL = 60  # seconds


def get_ddl_statements():
    """Return DDL statements for login_attempts table."""
    return [
        """CREATE TABLE IF NOT EXISTS login_attempts (
            username VARCHAR(255) PRIMARY KEY,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_locked_until ON login_attempts(locked_until)",
    ]


def _get_security_settings() -> dict:
    """Get security settings with a 60-second in-memory cache."""
    now = time.time()
    cached = _security_settings_cache.get("timestamp", 0)
    if _security_settings_cache and (now - cached) < _SECURITY_SETTINGS_TTL:
        return _security_settings_cache["settings"]

    try:
        from app.repositories.governance_repo import GovernanceRepository

        settings = GovernanceRepository().get_security_settings()
        _security_settings_cache["settings"] = settings
        _security_settings_cache["timestamp"] = now
        return settings
    except Exception:
        return {}


def _get_lockout_duration_minutes() -> int:
    """Get lockout duration in minutes from security_settings."""
    return int(_get_security_settings().get("lockout_duration_minutes", 15))


def _get_max_login_attempts() -> int:
    """Get max login attempts from security_settings."""
    return int(_get_security_settings().get("max_login_attempts", 5))


def _check_login_lockout(username: str) -> tuple[bool, Optional[str]]:
    """Check if account is temporarily locked. Returns (is_locked, error_message).

    Degrades gracefully on DB failure: logs warning and allows login (no lockout).
    """
    from app.repositories.database import Database, get_param_placeholder

    p = get_param_placeholder()

    try:
        db = Database()
        row = db.fetch_one(
            f"SELECT attempt_count, locked_until FROM login_attempts WHERE username = {p}",
            (username,),
        )

        if not row:
            return False, None

        locked_until = row.get("locked_until")
        if locked_until:
            if isinstance(locked_until, str):
                locked_until = datetime.fromisoformat(locked_until)
            if locked_until > datetime.utcnow():
                remaining = int((locked_until - datetime.utcnow()).total_seconds() / 60) + 1
                return True, f"Account temporarily locked. Try again in {remaining} minutes."
            else:
                # Lockout expired — reset
                db.execute(
                    f"DELETE FROM login_attempts WHERE username = {p}",
                    (username,),
                )
                return False, None

        return False, None
    except Exception as e:
        logger.warning(f"Login lockout check failed for {username}: {e}")
        return False, None


def _record_failed_login(username: str) -> None:
    """Record a failed login attempt and lock if threshold exceeded.

    Uses SELECT + INSERT/UPDATE instead of ON CONFLICT for SQLite compatibility.
    Degrades gracefully on DB failure: logs warning and continues.
    """
    from app.repositories.database import Database, get_param_placeholder

    max_attempts = _get_max_login_attempts()
    lockout_minutes = _get_lockout_duration_minutes()
    p = get_param_placeholder()

    try:
        db = Database()

        # Check existing record
        row = db.fetch_one(
            f"SELECT attempt_count FROM login_attempts WHERE username = {p}",
            (username,),
        )

        if row:
            new_count = row["attempt_count"] + 1
            db.execute(
                f"UPDATE login_attempts SET attempt_count = {p} WHERE username = {p}",
                (new_count, username),
            )
        else:
            new_count = 1
            db.execute(
                f"INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES ({p}, {p}, NULL)",
                (username, 1),
            )

        if new_count >= max_attempts:
            locked_until = datetime.utcnow() + timedelta(minutes=lockout_minutes)
            db.execute(
                f"UPDATE login_attempts SET locked_until = {p} WHERE username = {p}",
                (locked_until, username),
            )
            logger.warning(f"Account locked for {username} after {max_attempts} failed attempts")
    except Exception as e:
        logger.warning(f"Failed to record login attempt for {username}: {e}")


def _clear_failed_logins(username: str) -> None:
    """Clear failed login attempts after successful login."""
    from app.repositories.database import Database, get_param_placeholder

    p = get_param_placeholder()

    try:
        db = Database()
        db.execute(
            f"DELETE FROM login_attempts WHERE username = {p}",
            (username,),
        )
    except Exception as e:
        logger.warning(f"Failed to clear login attempts for {username}: {e}")


# Session token expiration default (overridden by security_settings DB)
SESSION_EXPIRATION_HOURS = 24


def _get_session_timeout_hours() -> float:
    """Get session timeout from security_settings, falling back to 24h."""
    try:
        settings = _get_security_settings()
        timeout_minutes = settings.get("session_timeout", SESSION_EXPIRATION_HOURS * 60)
        return float(timeout_minutes) / 60.0
    except Exception:
        return float(SESSION_EXPIRATION_HOURS)


class AuthService:
    """Service for authentication-related business logic."""

    def __init__(self, user_repo: Optional[UserRepository] = None):
        """
        Initialize service.

        Args:
            user_repo: Optional UserRepository instance for dependency injection.
        """
        self.user_repo = user_repo or UserRepository()

    def login(
        self, username: str, password: str, password_verify_func
    ) -> tuple[Optional[dict], Optional[str]]:
        """
        Authenticate a user and create a session.

        Args:
            username: Username.
            password: Plain text password.
            password_verify_func: Function to verify password hash.

        Returns:
            Tuple[Optional[Dict], Optional[str]]: (User data, Session token) or (None, error message).
        """
        # Check lockout before attempting login
        is_locked, lockout_msg = _check_login_lockout(username)
        if is_locked:
            return None, lockout_msg

        # Get user by username
        user = self.user_repo.get_user_by_username(username)

        if not user:
            logger.warning("Login failed: invalid credentials")
            _record_failed_login(username)
            return None, "Invalid username or password"

        if not user.get("is_active"):
            logger.warning("Login failed: account disabled")
            return None, "Account is disabled"

        # Verify password
        if not password_verify_func(password, user.get("password_hash", "")):
            logger.warning("Login failed: invalid credentials")
            _record_failed_login(username)
            return None, "Invalid username or password"

        # Successful login — clear failed attempts
        _clear_failed_logins(username)

        # Create session token
        token = secrets.token_hex(32)
        timeout_hours = _get_session_timeout_hours()
        expires_at = datetime.utcnow() + timedelta(hours=timeout_hours)

        if not self.user_repo.create_session(user["id"], token, expires_at):
            logger.error(f"Failed to create session for user - {username}")
            return None, "Failed to create session"

        # Update last login
        self.user_repo.update_last_login(user["id"])

        logger.info(f"User logged in: {username}")

        # Check if user must change password
        must_change_password = bool(user.get("must_change_password"))

        return {
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email"),
            "role": user["role"],
            "must_change_password": must_change_password,
        }, token

    def logout(self, token: str) -> bool:
        """
        Logout a user by deleting their session.

        Args:
            token: Session token.

        Returns:
            bool: True if successful.
        """
        return self.user_repo.delete_session(token)

    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        password_verify_func,
        password_hash_func,
    ) -> tuple[bool, Optional[str]]:
        """
        Change user password.

        Args:
            user_id: User ID.
            current_password: Current password for verification.
            new_password: New password to set.
            password_verify_func: Function to verify password hash.
            password_hash_func: Function to hash new password.

        Returns:
            Tuple[bool, Optional[str]]: (Success, Error message or None).
        """
        # Get user
        user = self.user_repo.get_user_by_id(user_id)
        if not user:
            return False, "User not found"

        # Verify current password
        if not password_verify_func(current_password, user.get("password_hash", "")):
            return False, "Current password is incorrect"

        # Validate new password
        if len(new_password) < 8:
            return False, "New password must be at least 8 characters"

        if new_password == current_password:
            return False, "New password must be different from current password"

        # Hash and update password
        new_password_hash = password_hash_func(new_password)
        if not self.user_repo.update_password(user_id, new_password_hash):
            return False, "Failed to update password"

        logger.info(f"Password changed for user ID: {user_id}")
        return True, None

    def get_session(self, token: str) -> Optional[dict]:
        """
        Get session data by token.

        Args:
            token: Session token.

        Returns:
            Optional[Dict]: Session data or None.
        """
        return self.user_repo.get_session_by_token(token)

    def validate_session(self, token: str) -> tuple[bool, Optional[dict]]:
        """
        Validate a session token and return session data.

        Args:
            token: Session token.

        Returns:
            Tuple[bool, Optional[Dict]]: (Is valid, Session data or error dict).
        """
        if not token:
            return False, {"error": "Authentication required"}

        session = self.get_session(token)
        if not session:
            return False, {"error": "Invalid or expired session"}

        if self._is_session_expired(session):
            return False, {"error": "Session expired"}

        return True, session

    @staticmethod
    def _is_session_expired(session: dict) -> bool:
        """Check if a session has expired. Shared by all auth methods."""
        expires_at = session.get("expires_at")
        if not expires_at:
            return False
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        return expires_at < datetime.utcnow()

    def get_user_profile(self, user_id: int) -> Optional[dict]:
        """
        Get user profile.

        Args:
            user_id: User ID.

        Returns:
            Optional[Dict]: User profile or None.
        """
        user = self.user_repo.get_user_by_id(user_id)
        if user:
            # Remove sensitive data
            user.pop("password_hash", None)
        return user

    def is_admin(self, token: str) -> bool:
        """
        Check if the session belongs to an admin user.

        Args:
            token: Session token.

        Returns:
            bool: True if admin.
        """
        session = self.get_session(token)
        return session and session.get("role") == "admin"

    def require_auth(self, token: str) -> tuple[bool, Optional[dict]]:
        """
        Require authentication and return session data.

        Delegates to validate_session() to ensure consistent expiry logic.

        Args:
            token: Session token.

        Returns:
            Tuple[bool, Optional[Dict]]: (Is authenticated, Session data or error).
        """
        return self.validate_session(token)

    def require_admin(self, token: str) -> tuple[bool, Optional[dict]]:
        """
        Require admin role and return session data.

        Args:
            token: Session token.

        Returns:
            Tuple[bool, Optional[Dict]]: (Is admin, Session data or error).
        """
        is_auth, session = self.require_auth(token)
        if not is_auth:
            return False, session

        if session.get("role") != "admin":
            return False, {"error": "Admin access required"}

        return True, session
