"""Unit tests for AuthService.

Note: Some tests directly invoke private module-level functions
(_check_login_lockout, _record_failed_login, etc.) to verify edge cases
(expired lockouts, max attempts, cache TTL) that are difficult to trigger
through the public login() interface alone. The public interface is also
tested in TestAuthService.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service import (
    SESSION_EXPIRATION_HOURS,
    AuthService,
    _check_login_lockout,
    _clear_failed_logins,
    _get_lockout_duration_minutes,
    _get_max_login_attempts,
    _get_security_settings,
    _record_failed_login,
    _security_settings_cache,
)


class TestSecuritySettings:
    """Test security settings caching and retrieval."""

    def setup_method(self):
        _security_settings_cache.clear()

    def test_get_security_settings_cached(self):
        _security_settings_cache["settings"] = {"lockout_duration_minutes": 30}
        _security_settings_cache["timestamp"] = time.time()

        result = _get_security_settings()
        assert result["lockout_duration_minutes"] == 30

    def test_get_security_settings_expired_cache(self):
        _security_settings_cache["settings"] = {"lockout_duration_minutes": 30}
        _security_settings_cache["timestamp"] = time.time() - 120  # Expired

        with patch("app.repositories.governance_repo.GovernanceRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_security_settings.return_value = {"lockout_duration_minutes": 15}
            mock_repo_cls.return_value = mock_repo

            result = _get_security_settings()
            assert result["lockout_duration_minutes"] == 15

    def test_get_security_settings_no_cache(self):
        with patch("app.repositories.governance_repo.GovernanceRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_security_settings.return_value = {"max_login_attempts": 3}
            mock_repo_cls.return_value = mock_repo

            result = _get_security_settings()
            assert result["max_login_attempts"] == 3

    def test_get_security_settings_db_failure(self):
        with patch("app.repositories.governance_repo.GovernanceRepository") as mock_repo_cls:
            mock_repo_cls.side_effect = Exception("DB error")
            result = _get_security_settings()
            assert result == {}

    def test_get_lockout_duration_default(self):
        _security_settings_cache["settings"] = {}
        _security_settings_cache["timestamp"] = time.time()

        assert _get_lockout_duration_minutes() == 15

    def test_get_lockout_duration_custom(self):
        _security_settings_cache["settings"] = {"lockout_duration_minutes": 30}
        _security_settings_cache["timestamp"] = time.time()

        assert _get_lockout_duration_minutes() == 30

    def test_get_max_login_attempts_default(self):
        _security_settings_cache["settings"] = {}
        _security_settings_cache["timestamp"] = time.time()

        assert _get_max_login_attempts() == 5

    def test_get_max_login_attempts_custom(self):
        _security_settings_cache["settings"] = {"max_login_attempts": 10}
        _security_settings_cache["timestamp"] = time.time()

        assert _get_max_login_attempts() == 10


class TestLoginLockout:
    """Test login lockout checking."""

    def setup_method(self):
        _security_settings_cache.clear()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_no_lockout_record(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is False
        assert msg is None

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_locked_account_datetime(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is True
        assert "temporarily locked" in msg

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_locked_account_iso_string(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        future = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        ).isoformat()
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": future,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is True

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_expired_lockout_cleared(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        mock_db.fetch_one.return_value = {
            "attempt_count": 5,
            "locked_until": past,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is False
        assert msg is None
        # Verify DELETE was called to clear expired lockout
        mock_db.execute.assert_called_once()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_no_lockout_none_locked_until(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "attempt_count": 2,
            "locked_until": None,
        }
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is False
        assert msg is None

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_lockout_db_error_graceful(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = Exception("DB error")
        mock_db_cls.return_value = mock_db

        is_locked, msg = _check_login_lockout("testuser")
        assert is_locked is False
        assert msg is None


class TestRecordFailedLogin:
    """Test recording failed login attempts."""

    def setup_method(self):
        _security_settings_cache.clear()
        _security_settings_cache["settings"] = {
            "max_login_attempts": 3,
            "lockout_duration_minutes": 15,
        }
        _security_settings_cache["timestamp"] = time.time()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_first_failed_login(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None
        mock_db_cls.return_value = mock_db

        _record_failed_login("testuser")

        # Should INSERT with count=1
        mock_db.execute.assert_called_once()
        call_args = mock_db.execute.call_args[0][1]
        assert call_args[1] == 1

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_subsequent_failed_login(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"attempt_count": 1}
        mock_db_cls.return_value = mock_db

        _record_failed_login("testuser")

        # Should UPDATE count to 2 (below max_attempts=3, so no lockout)
        mock_db.execute.assert_called_once()
        call_args = mock_db.execute.call_args[0][1]
        assert call_args[0] == 2

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_lockout_after_max_attempts(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        # Simulate reaching max attempts
        mock_db.fetch_one.return_value = {"attempt_count": 2}
        mock_db_cls.return_value = mock_db

        _record_failed_login("testuser")

        # Should have two execute calls: UPDATE count and UPDATE locked_until
        assert mock_db.execute.call_count == 2

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_record_failed_login_db_error(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = Exception("DB error")
        mock_db_cls.return_value = mock_db

        # Should not raise
        _record_failed_login("testuser")


class TestClearFailedLogins:
    """Test clearing failed login attempts."""

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_clear_success(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        _clear_failed_logins("testuser")
        mock_db.execute.assert_called_once()

    @patch("app.repositories.database.get_param_placeholder", return_value="?")
    @patch("app.repositories.database.Database")
    def test_clear_db_error(self, mock_db_cls, mock_placeholder):
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB error")
        mock_db_cls.return_value = mock_db

        # Should not raise
        _clear_failed_logins("testuser")


class TestAuthService:
    """Test AuthService business logic."""

    def _make_service(self):
        mock_repo = MagicMock()
        svc = AuthService(user_repo=mock_repo)
        return svc, mock_repo

    def test_init_default_repo(self):
        with patch("app.services.auth_service.UserRepository"):
            svc = AuthService()
            assert svc.user_repo is not None

    def test_login_success(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = {
            "id": 1,
            "username": "testuser",
            "password_hash": "hash",
            "is_active": True,
            "role": "user",
            "email": "test@example.com",
            "must_change_password": False,
            "avatar_url": None,
        }
        mock_repo.create_session.return_value = True

        with (
            patch("app.services.auth_service._check_login_lockout", return_value=(False, None)),
            patch("app.services.auth_service._clear_failed_logins"),
            patch("app.services.auth_service._get_session_timeout_hours", return_value=24),
        ):
            user_data, token = svc.login("testuser", "password", lambda p, h: True)

            assert user_data is not None
            assert user_data["username"] == "testuser"
            assert token is not None
            assert len(token) == 64  # hex string of 32 bytes

    def test_login_locked_account(self):
        svc, mock_repo = self._make_service()

        with patch("app.services.auth_service._check_login_lockout", return_value=(True, "Locked")):
            user_data, error = svc.login("testuser", "password", lambda p, h: True)
            assert user_data is None
            assert error == "Locked"

    def test_login_user_not_found(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = None

        with (
            patch("app.services.auth_service._check_login_lockout", return_value=(False, None)),
            patch("app.services.auth_service._record_failed_login"),
        ):
            user_data, error = svc.login("nonexistent", "password", lambda p, h: True)
            assert user_data is None
            assert "Invalid" in error

    def test_login_inactive_user(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = {
            "id": 1,
            "username": "testuser",
            "is_active": False,
        }

        with patch("app.services.auth_service._check_login_lockout", return_value=(False, None)):
            user_data, error = svc.login("testuser", "password", lambda p, h: True)
            assert user_data is None
            assert "disabled" in error

    def test_login_wrong_password(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = {
            "id": 1,
            "username": "testuser",
            "password_hash": "hash",
            "is_active": True,
        }

        with (
            patch("app.services.auth_service._check_login_lockout", return_value=(False, None)),
            patch("app.services.auth_service._record_failed_login"),
        ):
            user_data, error = svc.login("testuser", "wrongpass", lambda p, h: False)
            assert user_data is None
            assert "Invalid" in error

    def test_login_session_creation_failure(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = {
            "id": 1,
            "username": "testuser",
            "password_hash": "hash",
            "is_active": True,
            "role": "user",
        }
        mock_repo.create_session.return_value = False

        with (
            patch("app.services.auth_service._check_login_lockout", return_value=(False, None)),
            patch("app.services.auth_service._clear_failed_logins"),
            patch("app.services.auth_service._get_session_timeout_hours", return_value=24),
        ):
            user_data, error = svc.login("testuser", "password", lambda p, h: True)
            assert user_data is None
            assert "session" in error.lower()

    def test_login_must_change_password(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_username.return_value = {
            "id": 1,
            "username": "testuser",
            "password_hash": "hash",
            "is_active": True,
            "role": "user",
            "must_change_password": True,
        }
        mock_repo.create_session.return_value = True

        with (
            patch("app.services.auth_service._check_login_lockout", return_value=(False, None)),
            patch("app.services.auth_service._clear_failed_logins"),
            patch("app.services.auth_service._get_session_timeout_hours", return_value=24),
        ):
            user_data, token = svc.login("testuser", "password", lambda p, h: True)
            assert user_data["must_change_password"] is True

    def test_logout(self):
        svc, mock_repo = self._make_service()
        mock_repo.delete_session.return_value = True

        result = svc.logout("token123")
        assert result is True
        mock_repo.delete_session.assert_called_once_with("token123")

    def test_change_password_success(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "password_hash": "old_hash",
        }
        mock_repo.update_password.return_value = True

        # Use password that meets policy requirements (uppercase, lowercase, number)
        success, error, error_type = svc.change_password(
            user_id=1,
            current_password="Oldpass123",
            new_password="Newpass123",
            password_verify_func=lambda p, h: True,
            password_hash_func=lambda p: "new_hash",
        )
        assert success is True
        assert error is None
        assert error_type is None

    def test_change_password_user_not_found(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = None

        success, error, error_type = svc.change_password(
            user_id=999,
            current_password="old",
            new_password="new123456",
            password_verify_func=lambda p, h: True,
            password_hash_func=lambda p: "hash",
        )
        assert success is False
        assert "not found" in error.lower()
        assert error_type is not None

    def test_change_password_wrong_current(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "password_hash": "old_hash",
        }

        success, error, error_type = svc.change_password(
            user_id=1,
            current_password="wrong",
            new_password="new123456",
            password_verify_func=lambda p, h: False,
            password_hash_func=lambda p: "hash",
        )
        assert success is False
        assert "incorrect" in error.lower()
        assert error_type is not None

    def test_change_password_too_short(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "password_hash": "old_hash",
        }

        success, error, error_type = svc.change_password(
            user_id=1,
            current_password="oldpass",
            new_password="short",
            password_verify_func=lambda p, h: True,
            password_hash_func=lambda p: "hash",
        )
        assert success is False
        assert "8 characters" in error
        # The change-password flow preserves the "New" context.
        assert "new" in error.lower()
        assert error_type is not None

    def test_change_password_same_as_current(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "password_hash": "old_hash",
        }

        # Use password that meets policy requirements
        success, error, error_type = svc.change_password(
            user_id=1,
            current_password="Samepass123",
            new_password="Samepass123",
            password_verify_func=lambda p, h: True,
            password_hash_func=lambda p: "hash",
        )
        assert success is False
        assert "different" in error.lower()
        assert error_type is not None

    def test_change_password_update_failure(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "password_hash": "old_hash",
        }
        mock_repo.update_password.return_value = False

        # Use password that meets policy requirements
        success, error, error_type = svc.change_password(
            user_id=1,
            current_password="Oldpass123",
            new_password="Newpass123",
            password_verify_func=lambda p, h: True,
            password_hash_func=lambda p: "new_hash",
        )
        assert success is False
        assert "update" in error.lower()
        assert error_type is not None

    def test_get_session(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_session_by_token.return_value = {"token": "abc", "user_id": 1}

        result = svc.get_session("abc")
        assert result["token"] == "abc"

    def test_validate_session_valid(self):
        svc, mock_repo = self._make_service()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "expires_at": future,
        }

        is_valid, session = svc.validate_session("abc")
        assert is_valid is True
        assert session["token"] == "abc"

    def test_validate_session_empty_token(self):
        svc, mock_repo = self._make_service()

        is_valid, session = svc.validate_session("")
        assert is_valid is False
        assert "Authentication required" in session["error"]

    def test_validate_session_none_token(self):
        svc, mock_repo = self._make_service()

        is_valid, session = svc.validate_session(None)
        assert is_valid is False

    def test_validate_session_no_session(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_session_by_token.return_value = None

        is_valid, session = svc.validate_session("invalid_token")
        assert is_valid is False
        assert "Invalid" in session["error"]

    def test_validate_session_expired(self):
        svc, mock_repo = self._make_service()
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "expires_at": past,
        }

        is_valid, session = svc.validate_session("abc")
        assert is_valid is False
        assert "expired" in session["error"].lower()

    def test_validate_session_expired_string(self):
        svc, mock_repo = self._make_service()
        past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "expires_at": past,
        }

        is_valid, session = svc.validate_session("abc")
        assert is_valid is False

    def test_is_session_expired_no_expires(self):
        assert AuthService._is_session_expired({}) is False

    def test_is_session_expired_none_expires(self):
        assert AuthService._is_session_expired({"expires_at": None}) is False

    def test_is_session_expired_future(self):
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        assert AuthService._is_session_expired({"expires_at": future}) is False

    def test_is_session_expired_past(self):
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        assert AuthService._is_session_expired({"expires_at": past}) is True

    def test_is_session_expired_string_past(self):
        past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
        assert AuthService._is_session_expired({"expires_at": past}) is True

    def test_get_user_profile(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = {
            "id": 1,
            "username": "testuser",
            "password_hash": "secret_hash",
        }

        result = svc.get_user_profile(1)
        assert "password_hash" not in result
        assert result["username"] == "testuser"

    def test_get_user_profile_not_found(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_user_by_id.return_value = None

        result = svc.get_user_profile(999)
        assert result is None

    def test_is_admin_true(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_session_by_token.return_value = {"role": "admin"}

        assert svc.is_admin("admin_token") is True

    def test_is_admin_false(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_session_by_token.return_value = {"role": "user"}

        assert svc.is_admin("user_token") is False

    def test_is_admin_no_session(self):
        svc, mock_repo = self._make_service()
        mock_repo.get_session_by_token.return_value = None

        assert svc.is_admin("invalid") is False

    def test_require_auth(self):
        svc, mock_repo = self._make_service()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "expires_at": future,
        }

        is_auth, session = svc.require_auth("abc")
        assert is_auth is True

    def test_require_admin_success(self):
        svc, mock_repo = self._make_service()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "role": "admin",
            "expires_at": future,
        }

        is_admin, session = svc.require_admin("abc")
        assert is_admin is True

    def test_require_admin_not_admin(self):
        svc, mock_repo = self._make_service()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        mock_repo.get_session_by_token.return_value = {
            "token": "abc",
            "role": "user",
            "expires_at": future,
        }

        is_admin, session = svc.require_admin("abc")
        assert is_admin is False
        assert "Admin access required" in session["error"]

    def test_require_admin_no_auth(self):
        svc, mock_repo = self._make_service()

        is_admin, session = svc.require_admin("")
        assert is_admin is False
