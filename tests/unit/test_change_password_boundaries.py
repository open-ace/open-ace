"""Boundary tests for change-password lockout logic.

Tests verify edge cases:
- Nth failure triggers lockout (not N-1)
- Lockout expiry timing
- Behavior at exact threshold
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service import (
    _check_change_password_lockout,
    _clear_change_password_failures,
    _get_max_login_attempts,
    _record_change_password_failure,
    _security_settings_cache,
)


class TestThresholdBoundary:
    """Test lockout triggers at exact threshold."""

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
        mock_cursor.fetchone.return_value = fetch_one_result

        mock_db.connection.return_value = mock_conn
        return mock_db

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_nth_failure_triggers_lockout(self, mock_db_cls, mock_is_pg):
        """The Nth failure should trigger lockout (where N = max_login_attempts)."""
        # Set threshold to 5
        _security_settings_cache["settings"] = {"max_login_attempts": 5}
        _security_settings_cache["timestamp"] = time.time()

        mock_db = self._make_mock_db(fetch_one_result=(4,))
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 5  # Nth attempt
        assert is_locked is True  # Should be locked

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_n_minus_1_failure_no_lockout(self, mock_db_cls, mock_is_pg):
        """N-1 failures should not trigger lockout."""
        # Set threshold to 5
        _security_settings_cache["settings"] = {"max_login_attempts": 5}
        _security_settings_cache["timestamp"] = time.time()

        mock_db = self._make_mock_db(fetch_one_result=(3,))
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 4  # N-1 attempt
        assert is_locked is False  # Should NOT be locked

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_already_over_threshold(self, mock_db_cls, mock_is_pg):
        """If somehow over threshold, should report as locked."""
        _security_settings_cache["settings"] = {"max_login_attempts": 5}
        _security_settings_cache["timestamp"] = time.time()

        mock_db = self._make_mock_db(fetch_one_result=(5,))
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 6
        assert is_locked is True


class TestLockoutTimeBoundary:
    """Test lockout expiry timing."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_lockout_just_expired(self, mock_db_cls, mock_placeholder):
        """Lockout that just expired should be cleared and allow operation."""
        mock_db = MagicMock()
        # Locked_until is 1 second in the past
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": past,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False
        # Should have called delete to clear
        mock_db.execute.assert_called()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_lockout_one_second_remaining(self, mock_db_cls, mock_placeholder):
        """Lockout with 1 second remaining should still be locked."""
        mock_db = MagicMock()
        # Locked_until is 1 second in the future
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is True
        assert "temporarily locked" in msg

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_lockout_exactly_at_boundary(self, mock_db_cls, mock_placeholder):
        """Lockout exactly at expiry moment."""
        mock_db = MagicMock()
        # locked_until is exactly now (boundary case)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": now,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        # At exact moment, should be considered expired (not locked)
        assert is_locked is False


class TestLockoutExpiryReset:
    """Test behavior after lockout expires."""

    def setup_method(self):
        _security_settings_cache.clear()

    def _make_mock_db(self, fetch_one_result=None):
        """Helper to create a properly mocked Database with connection context."""
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = fetch_one_result

        mock_db.connection.return_value = mock_conn
        return mock_db

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_first_failure_after_expiry_starts_from_one(self, mock_db_cls, mock_is_pg):
        """After lockout expires, first failure should start count from 1.

        This verifies that the delete of expired lockout properly resets state.
        """
        mock_db = self._make_mock_db(fetch_one_result=None)
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 1  # Should start from 1, not threshold+1
        assert is_locked is False


class TestLockoutDuringSuccess:
    """Test successful password change during lockout period."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_success_clears_failure_record(self, mock_db_cls, mock_placeholder):
        """Successful password change should clear failure record even during lockout.

        Note: In the actual flow, locked users can't reach the success path,
        but this tests the clear function works independently.
        """
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        result = _clear_change_password_failures(123)
        assert result is True
        mock_db.execute.assert_called()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_locked_user_attempts_change(self, mock_db_cls, mock_placeholder):
        """Locked user attempting change should be rejected before service call."""
        mock_db = MagicMock()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is True
        # In actual endpoint, this returns 429 before calling service
