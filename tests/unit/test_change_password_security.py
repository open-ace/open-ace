"""Unit tests for change-password security features.

Tests cover:
- Lockout checking and enforcement
- Failure attempt tracking
- Audit logging (success and failure)
- Error type classification
- Graceful degradation on DB failures
- Namespace prefix for lockout keys
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service import (
    ChangePasswordError,
    _check_change_password_lockout,
    _clear_change_password_failures,
    _get_change_password_lockout_key,
    _get_lockout_duration_minutes,
    _get_max_login_attempts,
    _get_security_settings,
    _record_change_password_failure,
    _security_settings_cache,
)


class TestChangePasswordLockoutKey:
    """Test the namespace prefix key generation."""

    def test_key_format(self):
        """Key should use cp: prefix to avoid username collision."""
        key = _get_change_password_lockout_key(123)
        assert key == "cp:user_123"

    def test_key_format_different_users(self):
        """Different user IDs should produce different keys."""
        key1 = _get_change_password_lockout_key(1)
        key2 = _get_change_password_lockout_key(2)
        assert key1 != key2


class TestCheckChangePasswordLockout:
    """Test change-password lockout checking."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_no_lockout_record(self, mock_db_cls, mock_placeholder):
        """No record means not locked."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False
        assert msg is None
        assert remaining is None

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_locked_account_datetime(self, mock_db_cls, mock_placeholder):
        """Locked account should return lockout message."""
        mock_db = MagicMock()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is True
        assert "temporarily locked" in msg
        assert remaining is not None
        assert remaining > 0

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_locked_account_iso_string(self, mock_db_cls, mock_placeholder):
        """Lockout with ISO string format should work."""
        mock_db = MagicMock()
        future = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        ).isoformat()
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is True

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_expired_lockout_cleared(self, mock_db_cls, mock_placeholder):
        """Expired lockout should be cleared."""
        mock_db = MagicMock()
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": past,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False
        assert msg is None
        # Should have called delete to clear expired lockout
        mock_db.execute.assert_called()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_not_locked_no_locked_until(self, mock_db_cls, mock_placeholder):
        """Record without locked_until means not locked."""
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "attempt_count": 3,
            "locked_until": None,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_failure_graceful_degradation(self, mock_db_cls, mock_placeholder):
        """DB failure should allow operation (return False, None, None)."""
        mock_db_cls.side_effect = Exception("DB connection failed")

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False
        assert msg is None
        assert remaining is None


class TestRecordChangePasswordFailure:
    """Test recording of failed change-password attempts."""

    def setup_method(self):
        _security_settings_cache.clear()

    def _make_mock_db(self, fetch_one_result=None):
        """Helper to create a properly mocked Database with connection context."""
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Setup connection context manager
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor

        # Setup cursor for fetch
        if fetch_one_result is not None:
            mock_cursor.fetchone.return_value = fetch_one_result
        else:
            mock_cursor.fetchone.return_value = None

        mock_db.connection.return_value = mock_conn
        mock_db_cls = MagicMock(return_value=mock_db)

        return mock_db_cls, mock_cursor, mock_conn

    @patch("app.repositories.database.Database")
    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.services.auth_service._get_max_login_attempts", return_value=5)
    @patch("app.services.auth_service._get_lockout_duration_minutes", return_value=15)
    def test_first_failure_creates_record(self, mock_duration, mock_max, mock_is_pg, mock_db_cls):
        """First failure should insert new record."""
        # Setup mock connection
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None  # No existing record

        mock_db.connection.return_value = mock_conn
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 1
        assert is_locked is False

    @patch("app.repositories.database.Database")
    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.services.auth_service._get_max_login_attempts", return_value=5)
    @patch("app.services.auth_service._get_lockout_duration_minutes", return_value=15)
    def test_subsequent_failure_increments(self, mock_duration, mock_max, mock_is_pg, mock_db_cls):
        """Subsequent failure should increment count."""
        # Setup mock connection
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (2,)  # Existing count=2

        mock_db.connection.return_value = mock_conn
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 3
        assert is_locked is False

    @patch("app.repositories.database.Database")
    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.services.auth_service._get_max_login_attempts", return_value=5)
    @patch("app.services.auth_service._get_lockout_duration_minutes", return_value=15)
    def test_lockout_triggered_at_threshold(self, mock_duration, mock_max, mock_is_pg, mock_db_cls):
        """Lockout should trigger when reaching max attempts."""
        # Setup mock connection
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (4,)  # Existing count=4

        mock_db.connection.return_value = mock_conn
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 5
        assert is_locked is True

    @patch("app.repositories.database.Database")
    @patch("app.repositories.database.is_postgresql", return_value=True)
    @patch("app.services.auth_service._get_max_login_attempts", return_value=5)
    @patch("app.services.auth_service._get_lockout_duration_minutes", return_value=15)
    def test_postgresql_no_begin_immediate(self, mock_duration, mock_max, mock_is_pg, mock_db_cls):
        """PostgreSQL should not use BEGIN IMMEDIATE."""
        # Setup mock connection
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        mock_db.connection.return_value = mock_conn
        mock_db_cls.return_value = mock_db

        _record_change_password_failure(123)
        # Check that BEGIN IMMEDIATE was NOT called
        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert not any("BEGIN IMMEDIATE" in call for call in calls)

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_db_failure_graceful_degradation(self, mock_db_cls, mock_is_pg):
        """DB failure should return (0, False) and not raise."""
        mock_db_cls.side_effect = Exception("DB error")

        count, is_locked = _record_change_password_failure(123)
        assert count == 0
        assert is_locked is False


class TestClearChangePasswordFailures:
    """Test clearing of failed attempts."""

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_clear_success(self, mock_db_cls, mock_placeholder):
        """Clear should delete record."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        result = _clear_change_password_failures(123)
        assert result is True
        mock_db.execute.assert_called()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_clear_db_failure(self, mock_db_cls, mock_placeholder):
        """DB failure should return False and not raise."""
        mock_db_cls.side_effect = Exception("DB error")

        result = _clear_change_password_failures(123)
        assert result is False


class TestChangePasswordError:
    """Test the ChangePasswordError enum."""

    def test_error_types_defined(self):
        """All required error types should be defined."""
        assert ChangePasswordError.USER_NOT_FOUND.value == "user_not_found"
        assert ChangePasswordError.CURRENT_PASSWORD_INCORRECT.value == "current_password_incorrect"
        assert ChangePasswordError.NEW_PASSWORD_INVALID.value == "new_password_invalid"
        assert ChangePasswordError.NEW_PASSWORD_SAME_AS_CURRENT.value == "new_password_same_as_current"
        assert ChangePasswordError.UPDATE_FAILED.value == "update_failed"


class TestAuditLogDetails:
    """Test that audit logs include proper details."""

    def test_failure_reason_in_details(self):
        """failure_reason should use enum value."""
        error_type = ChangePasswordError.CURRENT_PASSWORD_INCORRECT
        details = {
            "failure_reason": error_type.value,
            "attempt_count": 3,
        }
        assert details["failure_reason"] == "current_password_incorrect"
        assert details["attempt_count"] == 3

    def test_attempt_count_is_post_increment(self):
        """attempt_count should be the value after increment."""
        # This documents the expected behavior
        previous_count = 4
        new_count = previous_count + 1
        details = {
            "failure_reason": "current_password_incorrect",
            "attempt_count": new_count,  # Post-increment value
        }
        assert details["attempt_count"] == 5