"""
Open ACE - AI Computing Explorer - Auth Service

Business logic for authentication and authorization.
"""

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import cast

from app.repositories.user_repo import UserRepository
from app.utils.validators import validate_password

logger = logging.getLogger(__name__)


class ChangePasswordError(Enum):
    """Enumeration of change-password error types.

    Used to distinguish between different failure reasons for:
    - Precise failure attempt tracking (only CURRENT_PASSWORD_INCORRECT counts)
    - Audit log failure_reason classification
    - Robust error handling (no string matching required)
    """

    USER_NOT_FOUND = "user_not_found"
    CURRENT_PASSWORD_INCORRECT = "current_password_incorrect"
    NEW_PASSWORD_INVALID = "new_password_invalid"
    NEW_PASSWORD_SAME_AS_CURRENT = "new_password_same_as_current"
    UPDATE_FAILED = "update_failed"


# Cache for security_settings queries (60 second TTL)
_security_settings_cache: dict = {}  # {"settings": dict, "timestamp": float}
_SECURITY_SETTINGS_TTL = 60  # seconds


def _get_security_settings() -> dict:
    """Get security settings with a 60-second in-memory cache."""
    now = time.time()
    cached = _security_settings_cache.get("timestamp", 0)
    if _security_settings_cache and (now - cached) < _SECURITY_SETTINGS_TTL:
        return cast("dict", _security_settings_cache["settings"])

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


def _check_login_lockout(username: str) -> tuple[bool, str | None]:
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
            if locked_until > datetime.now(timezone.utc).replace(tzinfo=None):
                remaining = (
                    int(
                        (
                            locked_until - datetime.now(timezone.utc).replace(tzinfo=None)
                        ).total_seconds()
                        / 60
                    )
                    + 1
                )
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
            locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                minutes=lockout_minutes
            )
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


# ============================================================================
# Change-Password Lockout Functions
# ============================================================================
# These functions implement rate limiting and lockout for the change-password
# endpoint, using a namespace prefix ("cp:") to distinguish from login attempts.
#
# Key format: "cp:user_{user_id}" (e.g., "cp:user_123")
# - "cp:" prefix ensures no collision with real usernames (validation regex
#   prohibits colons in usernames, see app/utils/validators.py)
# - Uses the same login_attempts table as login lockout
# - Independent from login lockout (separate counters and lockouts)
# ============================================================================


def _get_change_password_lockout_key(user_id: int) -> str:
    """Get the lockout key for change-password attempts.

    Uses namespace prefix "cp:" to distinguish from login attempts.
    The prefix format ensures no collision with real usernames because
    the username validation regex prohibits colons.

    Args:
        user_id: User ID.

    Returns:
        str: Lockout key in format "cp:user_{user_id}".
    """
    return f"cp:user_{user_id}"


def _check_change_password_lockout(user_id: int) -> tuple[bool, str | None, int | None]:
    """Check if change-password is temporarily locked for a user.

    Returns:
        Tuple of (is_locked, error_message, remaining_minutes).
        On DB failure, returns (False, None, None) to allow operation (graceful degradation).
    """
    from app.repositories.database import Database, get_param_placeholder

    p = get_param_placeholder()
    key = _get_change_password_lockout_key(user_id)

    try:
        db = Database()
        row = db.fetch_one(
            f"SELECT attempt_count, locked_until FROM login_attempts WHERE username = {p}",
            (key,),
        )

        if not row:
            return False, None, None

        locked_until = row.get("locked_until")
        if locked_until:
            if isinstance(locked_until, str):
                locked_until = datetime.fromisoformat(locked_until)
            if locked_until > datetime.now(timezone.utc).replace(tzinfo=None):
                remaining = (
                    int(
                        (
                            locked_until - datetime.now(timezone.utc).replace(tzinfo=None)
                        ).total_seconds()
                        / 60
                    )
                    + 1
                )
                return (
                    True,
                    f"Account temporarily locked. Try again in {remaining} minutes.",
                    remaining,
                )
            else:
                # Lockout expired — reset
                db.execute(
                    f"DELETE FROM login_attempts WHERE username = {p}",
                    (key,),
                )
                return False, None, None

        return False, None, None
    except Exception as e:
        logger.warning(f"Change-password lockout check failed for user {user_id}: {e}")
        return False, None, None


def _record_change_password_failure(user_id: int) -> tuple[int, bool]:
    """Record a failed change-password attempt and lock if threshold exceeded.

    Uses transaction (BEGIN IMMEDIATE for SQLite) to ensure atomicity.
    Returns (new_count, is_now_locked).
    On DB failure, returns (0, False) and logs warning (graceful degradation).
    """
    from app.repositories.database import Database, get_param_placeholder, is_postgresql

    max_attempts = _get_max_login_attempts()
    lockout_minutes = _get_lockout_duration_minutes()
    p = get_param_placeholder()
    key = _get_change_password_lockout_key(user_id)

    try:
        db = Database()

        # Use connection context manager to keep all operations on the same connection
        with db.connection() as conn:
            cursor = conn.cursor()

            # SQLite: use BEGIN IMMEDIATE for explicit write lock
            # PostgreSQL: default transaction behavior (auto BEGIN on first statement)
            if not is_postgresql():
                cursor.execute("BEGIN IMMEDIATE")

            try:
                # Check existing record
                cursor.execute(
                    f"SELECT attempt_count FROM login_attempts WHERE username = {p}",
                    (key,),
                )
                row = cursor.fetchone()

                if row:
                    # SQLite returns Row object, PostgreSQL returns tuple or dict
                    if isinstance(row, dict):
                        current_count = row["attempt_count"]
                    else:
                        current_count = row[0]
                    new_count = current_count + 1
                    cursor.execute(
                        f"UPDATE login_attempts SET attempt_count = {p} WHERE username = {p}",
                        (new_count, key),
                    )
                else:
                    new_count = 1
                    cursor.execute(
                        f"INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES ({p}, {p}, NULL)",
                        (key, 1),
                    )

                is_now_locked = False
                if new_count >= max_attempts:
                    locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                        minutes=lockout_minutes
                    )
                    cursor.execute(
                        f"UPDATE login_attempts SET locked_until = {p} WHERE username = {p}",
                        (locked_until, key),
                    )
                    is_now_locked = True
                    logger.warning(
                        f"Change-password locked for user {user_id} after {max_attempts} failed attempts"
                    )

                # Commit transaction
                conn.commit()
                return new_count, is_now_locked

            except Exception as e:
                # Rollback on error
                conn.rollback()
                raise e

    except Exception as e:
        logger.warning(f"Failed to record change-password failure for user {user_id}: {e}")
        return 0, False


def _clear_change_password_failures(user_id: int) -> bool:
    """Clear failed change-password attempts after successful password change.

    Returns:
        bool: True if successful, False on failure (logs warning, no exception).
    """
    from app.repositories.database import Database, get_param_placeholder

    p = get_param_placeholder()
    key = _get_change_password_lockout_key(user_id)

    try:
        db = Database()
        db.execute(
            f"DELETE FROM login_attempts WHERE username = {p}",
            (key,),
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to clear change-password attempts for user {user_id}: {e}")
        return False


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

    def __init__(self, user_repo: UserRepository | None = None):
        """
        Initialize service.

        Args:
            user_repo: Optional UserRepository instance for dependency injection.
        """
        self.user_repo = user_repo or UserRepository()

    def login(
        self, username: str, password: str, password_verify_func
    ) -> tuple[dict | None, str | None]:
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
        # Use local time to match database TIMESTAMP WITHOUT TIME ZONE behavior
        expires_at = datetime.now() + timedelta(hours=timeout_hours)

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
            "avatar_url": user.get("avatar_url"),
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
    ) -> tuple[bool, str | None, ChangePasswordError | None]:
        """
        Change user password.

        Args:
            user_id: User ID.
            current_password: Current password for verification.
            new_password: New password to set.
            password_verify_func: Function to verify password hash.
            password_hash_func: Function to hash new password.

        Returns:
            Tuple[bool, Optional[str], Optional[ChangePasswordError]]:
                (Success, Error message or None, Error type or None).
                Note: Return value changed from 2-tuple to 3-tuple.
                Callers must handle the third element (error_type).
        """
        # Get user
        user = self.user_repo.get_user_by_id(user_id)
        if not user:
            return False, "User not found", ChangePasswordError.USER_NOT_FOUND

        # Verify current password
        if not password_verify_func(current_password, user.get("password_hash", "")):
            return (
                False,
                "Current password is incorrect",
                ChangePasswordError.CURRENT_PASSWORD_INCORRECT,
            )

        # Validate new password with security policy
        settings = _get_security_settings()
        is_valid, error_msg = validate_password(new_password, policy_settings=settings)
        if not is_valid:
            # Restore the "New" context so the error is unambiguous in the
            # change-password flow, vs. the shared validator's generic phrasing.
            return (
                False,
                f"New {error_msg[0].lower()}{error_msg[1:]}",
                ChangePasswordError.NEW_PASSWORD_INVALID,
            )

        if new_password == current_password:
            return (
                False,
                "New password must be different from current password",
                ChangePasswordError.NEW_PASSWORD_SAME_AS_CURRENT,
            )

        # Hash and update password
        new_password_hash = password_hash_func(new_password)
        if not self.user_repo.update_password(user_id, new_password_hash):
            return False, "Failed to update password", ChangePasswordError.UPDATE_FAILED

        logger.info(f"Password changed for user ID: {user_id}")
        return True, None, None

    def get_session(self, token: str) -> dict | None:
        """
        Get session data by token.

        Args:
            token: Session token.

        Returns:
            Optional[Dict]: Session data or None.
        """
        return self.user_repo.get_session_by_token(token)

    def validate_session(self, token: str) -> tuple[bool, dict | None]:
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
        # Use local time to match database TIMESTAMP WITHOUT TIME ZONE behavior
        # Database stores times as local time without timezone info
        return bool(expires_at < datetime.now())

    def get_user_profile(self, user_id: int) -> dict | None:
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
        if session is None:
            return False
        return session.get("role") == "admin"

    def require_auth(self, token: str) -> tuple[bool, dict | None]:
        """
        Require authentication and return session data.

        Delegates to validate_session() to ensure consistent expiry logic.

        Args:
            token: Session token.

        Returns:
            Tuple[bool, Optional[Dict]]: (Is authenticated, Session data or error).
        """
        return self.validate_session(token)

    def require_admin(self, token: str) -> tuple[bool, dict | None]:
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

        if session is None or session.get("role") != "admin":
            return False, {"error": "Admin access required"}

        return True, session


def get_security_settings_cached() -> dict:
    """Public accessor for security settings (60-second in-memory cache).

    Thin wrapper over the module-private ``_get_security_settings`` so the
    public API is a regular function rather than a bare alias. The cache lives
    inside ``_get_security_settings`` and is preserved unchanged.
    """
    return _get_security_settings()
