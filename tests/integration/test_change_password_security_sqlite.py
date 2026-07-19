"""Integration tests for change-password security against real SQLite database.

Tests:
- End-to-end change-password lockout flow
- login_attempts table state changes
- Audit log persistence
- Independence from login lockout
- Transaction boundaries
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.services import auth_service
from app.services.auth_service import (
    ChangePasswordError,
    _check_change_password_lockout,
    _clear_change_password_failures,
    _get_change_password_lockout_key,
    _record_change_password_failure,
)


class TestChangePasswordLockoutIntegration:
    """Integration tests for change-password lockout via login_attempts table."""

    def _patch_db(self, tmp_db):
        """Patch Database() to use tmp_db's SQLite path."""
        import app.repositories.database as db_mod

        return patch.object(db_mod, "get_database_url", return_value=tmp_db.db_url)

    def test_no_lockout_initially(self, tmp_db):
        """New user has no change-password lockout."""
        auth_service._security_settings_cache.clear()

        with self._patch_db(tmp_db):
            is_locked, msg, remaining = _check_change_password_lockout(123)
            assert is_locked is False
            assert msg is None
            assert remaining is None

    def test_record_failure_increments(self, tmp_db):
        """Recording failure creates/increments attempt_count."""
        auth_service._security_settings_cache.clear()

        key = _get_change_password_lockout_key(123)

        with self._patch_db(tmp_db):
            count, is_locked = _record_change_password_failure(123)
            assert count == 1
            assert is_locked is False

            # Verify database state
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (key,)
            )
            assert row is not None
            assert row["attempt_count"] == 1

            # Second failure
            count, is_locked = _record_change_password_failure(123)
            assert count == 2

            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (key,)
            )
            assert row["attempt_count"] == 2

    def test_lockout_after_max_attempts(self, tmp_db):
        """Account is locked after max_login_attempts failures."""
        auth_service._security_settings_cache.clear()

        key = _get_change_password_lockout_key(456)

        with self._patch_db(tmp_db):
            max_attempts = auth_service._get_max_login_attempts()
            assert max_attempts > 0

            # Record max_attempts failures
            for i in range(max_attempts):
                count, is_locked = _record_change_password_failure(456)
                if i < max_attempts - 1:
                    assert is_locked is False
                else:
                    assert is_locked is True  # Last attempt triggers lockout

            # Check lockout
            is_locked, msg, remaining = _check_change_password_lockout(456)
            assert is_locked is True
            assert "temporarily locked" in msg.lower()
            assert remaining is not None

            # Verify locked_until is set
            row = tmp_db.fetch_one(
                "SELECT locked_until FROM login_attempts WHERE username = ?", (key,)
            )
            assert row["locked_until"] is not None

    def test_clear_failures(self, tmp_db):
        """Clearing failures removes the record."""
        auth_service._security_settings_cache.clear()

        key = _get_change_password_lockout_key(789)

        with self._patch_db(tmp_db):
            _record_change_password_failure(789)
            _record_change_password_failure(789)

            # Verify record exists
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (key,)
            )
            assert row is not None

            # Clear
            result = _clear_change_password_failures(789)
            assert result is True

            # Record should be gone
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (key,)
            )
            assert row is None

    def test_expired_lockout_allows_operation(self, tmp_db):
        """Expired lockout is auto-cleaned and allows operation."""
        auth_service._security_settings_cache.clear()

        key = _get_change_password_lockout_key(999)

        # Manually insert an expired lockout
        expired_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
        tmp_db.execute(
            "INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES (?, ?, ?)",
            (key, 5, expired_time.isoformat()),
        )

        with self._patch_db(tmp_db):
            is_locked, msg, remaining = _check_change_password_lockout(999)
            assert is_locked is False

            # Record should have been cleaned up
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (key,)
            )
            assert row is None


class TestChangePasswordAuditLog:
    """Integration tests for audit logging."""

    def _patch_db(self, tmp_db):
        """Patch Database() to use tmp_db's SQLite path."""
        import app.repositories.database as db_mod

        return patch.object(db_mod, "get_database_url", return_value=tmp_db.db_url)

    def test_audit_log_persisted_on_failure(self, tmp_db):
        """Audit log for failed change-password should be persisted."""
        auth_service._security_settings_cache.clear()

        with self._patch_db(tmp_db):
            audit_logger = AuditLogger()

            # Log a failed change-password
            audit_logger.log_action(
                action=AuditAction.USER_PASSWORD_CHANGE_FAILED,
                user_id=123,
                username="testuser",
                resource_type="user",
                resource_id="123",
                success=False,
                error_message="Current password is incorrect",
                details={
                    "failure_reason": "current_password_incorrect",
                    "attempt_count": 1,
                },
            )

            # Query audit logs
            logs = audit_logger.query(
                action="user_password_change_failed",
                limit=10,
            )
            assert len(logs) == 1
            assert logs[0].user_id == 123
            # success field: database stores 0, from_dict normalizes to False
            assert logs[0].success is False or logs[0].success == 0
            assert logs[0].details.get("failure_reason") == "current_password_incorrect"
            assert logs[0].details.get("attempt_count") == 1

    def test_audit_log_persisted_on_success(self, tmp_db):
        """Audit log for successful change-password should be persisted."""
        auth_service._security_settings_cache.clear()

        with self._patch_db(tmp_db):
            audit_logger = AuditLogger()

            # Log a successful change-password
            audit_logger.log_action(
                action=AuditAction.USER_PASSWORD_CHANGE,
                user_id=123,
                username="testuser",
                resource_type="user",
                resource_id="123",
                success=True,
            )

            # Query audit logs
            logs = audit_logger.query(
                action="user_password_change",
                limit=10,
            )
            assert len(logs) >= 1
            found = any(log.user_id == 123 for log in logs)
            assert found


class TestChangePasswordLoginIndependence:
    """Test that change-password lockout is independent from login lockout."""

    def _patch_db(self, tmp_db):
        """Patch Database() to use tmp_db's SQLite path."""
        import app.repositories.database as db_mod

        return patch.object(db_mod, "get_database_url", return_value=tmp_db.db_url)

    def test_change_password_lockout_does_not_affect_login(self, tmp_db):
        """Change-password lockout should not affect login lockout."""
        auth_service._security_settings_cache.clear()

        cp_key = _get_change_password_lockout_key(123)
        login_key = "testuser"

        with self._patch_db(tmp_db):
            # Create change-password lockout
            max_attempts = auth_service._get_max_login_attempts()
            for _ in range(max_attempts):
                _record_change_password_failure(123)

            # Verify change-password is locked
            is_locked, _, _ = _check_change_password_lockout(123)
            assert is_locked is True

            # Login lockout for same user should NOT be affected
            # (login uses username, change-password uses user_id with cp: prefix)
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (login_key,)
            )
            assert row is None  # No login lockout record

    def test_login_lockout_does_not_affect_change_password(self, tmp_db):
        """Login lockout should not affect change-password lockout."""
        auth_service._security_settings_cache.clear()

        auth_service._security_settings_cache.clear()
        from app.services.auth_service import _record_failed_login

        cp_key = _get_change_password_lockout_key(123)
        login_key = "testuser"

        with self._patch_db(tmp_db):
            # Create login lockout
            max_attempts = auth_service._get_max_login_attempts()
            for _ in range(max_attempts):
                _record_failed_login(login_key)

            # Verify login is locked
            from app.services.auth_service import _check_login_lockout

            is_locked, _ = _check_login_lockout(login_key)
            assert is_locked is True

            # Change-password lockout should NOT be affected
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", (cp_key,)
            )
            assert row is None  # No change-password lockout record

    def test_different_users_independent(self, tmp_db):
        """Different users should have independent lockout states."""
        auth_service._security_settings_cache.clear()

        with self._patch_db(tmp_db):
            # Lock user 111
            max_attempts = auth_service._get_max_login_attempts()
            for _ in range(max_attempts):
                _record_change_password_failure(111)

            # User 222 should not be locked
            is_locked, _, _ = _check_change_password_lockout(222)
            assert is_locked is False

            # User 111 should be locked
            is_locked, _, _ = _check_change_password_lockout(111)
            assert is_locked is True


class TestNamespacePrefix:
    """Test namespace prefix behavior."""

    def test_key_format_matches_expected_pattern(self):
        """Key should match expected pattern."""
        key = _get_change_password_lockout_key(123)
        assert key.startswith("cp:user_")
        assert key == "cp:user_123"

    def test_key_different_from_username(self):
        """Key should not match typical username format."""
        key = _get_change_password_lockout_key(123)
        # Key contains colon which is not allowed in usernames
        assert ":" in key
        # Username validation regex: r"^[a-zA-Z0-9_\-一-鿿㐀-䶿]+$"
        # Colon is not in the allowed set