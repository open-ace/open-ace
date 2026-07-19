"""Degradation tests for change-password security features.

Tests verify graceful degradation when:
- DB connection fails during lockout check
- DB write fails during failure recording
- DB delete fails during failure clearing
- Audit log write fails
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.services.auth_service import (
    _check_change_password_lockout,
    _clear_change_password_failures,
    _record_change_password_failure,
    _security_settings_cache,
)


class TestLockoutCheckDegradation:
    """Test graceful degradation when lockout check fails."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_connection_failure_allows_operation(self, mock_db_cls, mock_placeholder):
        """DB failure during lockout check should allow operation to proceed."""
        mock_db_cls.side_effect = Exception("Connection refused")

        is_locked, msg, remaining = _check_change_password_lockout(123)
        # Should gracefully degrade - allow operation
        assert is_locked is False
        assert msg is None
        assert remaining is None

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_query_failure_allows_operation(self, mock_db_cls, mock_placeholder):
        """DB query failure should not block user."""
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = Exception("Query failed")
        mock_db_cls.return_value = mock_db

        is_locked, msg, remaining = _check_change_password_lockout(123)
        assert is_locked is False


class TestFailureRecordingDegradation:
    """Test graceful degradation when failure recording fails."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_write_failure_returns_zero(self, mock_db_cls, mock_placeholder, mock_is_pg):
        """DB write failure should return (0, False), not raise."""
        mock_db_cls.side_effect = Exception("Write failed")

        count, is_locked = _record_change_password_failure(123)
        assert count == 0
        assert is_locked is False

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_transaction_begin_failure_graceful(self, mock_db_cls, mock_placeholder, mock_is_pg):
        """Transaction begin failure should be handled gracefully."""
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("BEGIN IMMEDIATE failed")
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 0
        assert is_locked is False

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_transaction_rollback_on_failure(self, mock_db_cls, mock_placeholder, mock_is_pg):
        """Transaction should be rolled back on error."""
        mock_db = MagicMock()

        def execute_side_effect(sql, params=None):
            if "BEGIN" in sql:
                return None
            elif "SELECT" in sql or "fetch" in sql.lower():
                return None
            else:
                raise Exception("Update failed")

        mock_db.execute.side_effect = execute_side_effect
        mock_db.fetch_one.return_value = {"attempt_count": 1}
        mock_db_cls.return_value = mock_db

        count, is_locked = _record_change_password_failure(123)
        assert count == 0


class TestFailureClearDegradation:
    """Test graceful degradation when failure clearing fails."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_delete_failure_returns_false(self, mock_db_cls, mock_placeholder):
        """DB delete failure should return False, not raise."""
        mock_db_cls.side_effect = Exception("Delete failed")

        result = _clear_change_password_failures(123)
        assert result is False

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_db_delete_does_not_throw(self, mock_db_cls, mock_placeholder):
        """DB failure should not propagate exception."""
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("Delete failed")
        mock_db_cls.return_value = mock_db

        # Should not raise
        try:
            result = _clear_change_password_failures(123)
            assert result is False
        except Exception:
            pytest.fail("Should not raise exception")


class TestAuditLogDegradation:
    """Test graceful degradation when audit logging fails."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.Database")
    def test_audit_log_failure_does_not_raise(self, mock_db_cls):
        """Audit log failure should not propagate exception."""
        mock_db = MagicMock()
        mock_db.connection.side_effect = Exception("DB connection failed")
        mock_db_cls.return_value = mock_db

        audit_logger = AuditLogger(db=mock_db)

        # Should not raise
        try:
            result = audit_logger.log_action(
                action=AuditAction.USER_PASSWORD_CHANGE_FAILED,
                user_id=123,
                username="testuser",
                details={
                    "failure_reason": "current_password_incorrect",
                    "attempt_count": 1,
                },
            )
            assert result is False  # Returns False on failure
        except Exception:
            pytest.fail("Audit log failure should not raise")

    @patch("app.repositories.database.Database")
    def test_audit_log_db_error_handled(self, mock_db_cls):
        """Audit log should handle DB errors gracefully."""
        mock_db = MagicMock()
        # Simulate commit failure
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(side_effect=Exception("Commit failed"))
        mock_db.connection.return_value = conn
        mock_db_cls.return_value = mock_db

        audit_logger = AuditLogger(db=mock_db)

        result = audit_logger.log_action(
            action=AuditAction.USER_PASSWORD_CHANGE_FAILED,
            user_id=123,
        )
        assert result is False


class TestCombinedDegradationScenarios:
    """Test combined degradation scenarios."""

    def setup_method(self):
        _security_settings_cache.clear()

    def test_full_degradation_flow(self):
        """Verify all degradation paths work together.

        When DB is completely unavailable:
        1. Lockout check returns (False, None, None) - allows operation
        2. Failure recording returns (0, False) - no lockout
        3. Failure clearing returns False - graceful failure
        """
        with patch("app.repositories.database.Database") as mock_db_cls:
            mock_db_cls.side_effect = Exception("DB unavailable")

            # Step 1: Lockout check
            is_locked, msg, remaining = _check_change_password_lockout(123)
            assert is_locked is False

            # Step 2: Failure recording
            count, is_locked = _record_change_password_failure(123)
            assert count == 0
            assert is_locked is False

            # Step 3: Failure clearing
            result = _clear_change_password_failures(123)
            assert result is False

        # User can still proceed despite complete DB failure
        # This is the expected graceful degradation behavior