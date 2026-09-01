"""
Unit tests for Security Baseline Checker (Issue #1893, #2185)

Tests cover:
- Password checking (forbidden values, placeholders, length)
- Secret key checking
- Encryption key checking
- Root user authorization checking

Issue #2185: Security mode detection is now in security_mode.py
Issue #2331: Security mode now returns (mode, source) tuple
"""

import os

import pytest

# Import from security_baseline.py for baseline checking
from app.utils.security_baseline import (
    CheckResult,
    check_all,
    check_database_password,
    check_encryption_key,
    check_root_user,
    check_secret_key,
    is_forbidden_password,
    is_placeholder_password,
)

# Import from security_mode.py for unified mode detection (Issue #2185)
from app.utils.security_mode import (
    SecurityMode,
    SecurityModeSource,
    detect_security_mode,
    reset_security_mode_cache,
)


pytestmark = [
    pytest.mark.regression,
    pytest.mark.security,
    pytest.mark.issue(1893),
    pytest.mark.issue(2185),
    pytest.mark.issue(2331),
]


class TestDetectSecurityMode:
    """Tests for security mode detection."""

    def test_production_mode_from_openace_security_mode(self, monkeypatch):
        """Test production mode detection from OPENACE_SECURITY_MODE."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.EXPLICIT

    def test_pilot_mode_from_openace_security_mode(self, monkeypatch):
        """Test pilot mode detection from OPENACE_SECURITY_MODE."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "pilot")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.PILOT
        assert source == SecurityModeSource.EXPLICIT

    def test_development_mode_from_openace_security_mode(self, monkeypatch):
        """Test development mode detection from OPENACE_SECURITY_MODE."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.EXPLICIT

    def test_production_mode_from_flask_env(self, monkeypatch):
        """Test production mode detection from FLASK_ENV (backward compatibility)."""
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_SECURITY_MODE", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.INFERRED_FLASK_ENV

    def test_openace_security_mode_priority_over_flask_env(self, monkeypatch):
        """Test OPENACE_SECURITY_MODE has priority over FLASK_ENV."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.setenv("FLASK_ENV", "production")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.EXPLICIT

    def test_default_mode_returns_development(self, monkeypatch):
        """Test default returns development mode when not configured (Issue #2185 backward compat)."""
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_SECURITY_MODE", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)
        mode, source = detect_security_mode()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.DEFAULT

    def test_case_insensitive_mode_detection(self, monkeypatch):
        """Test case-insensitive mode detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "PRODUCTION")
        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.EXPLICIT


class TestForbiddenPasswords:
    """Tests for forbidden password detection."""

    def test_ace_secret_is_forbidden(self):
        """Test that ace-secret is in forbidden list."""
        assert is_forbidden_password("ace-secret") is True

    def test_dev_password_change_in_production_is_forbidden(self):
        """Test that dev-password-change-in-production is in forbidden list."""
        assert is_forbidden_password("dev-password-change-in-production") is True

    def test_change_me_is_forbidden(self):
        """Test that change-me is in forbidden list."""
        assert is_forbidden_password("change-me") is True

    def test_password_is_forbidden(self):
        """Test that password is in forbidden list."""
        assert is_forbidden_password("password") is True

    def test_admin_is_forbidden(self):
        """Test that admin is in forbidden list."""
        assert is_forbidden_password("admin") is True

    def test_postgres_is_forbidden(self):
        """Test that postgres is in forbidden list."""
        assert is_forbidden_password("postgres") is True

    def test_123456_is_forbidden(self):
        """Test that 123456 is in forbidden list."""
        assert is_forbidden_password("123456") is True

    def test_strong_password_not_forbidden(self):
        """Test that strong password is not forbidden."""
        assert is_forbidden_password("my-strong-password-123") is False

    def test_case_insensitive_forbidden_check(self):
        """Test that forbidden check is case-insensitive."""
        assert is_forbidden_password("ACE-SECRET") is True
        assert is_forbidden_password("Password") is True


class TestPlaceholderPasswords:
    """Tests for placeholder password detection."""

    def test_replace_with_random_prefix(self):
        """Test detection of replace-with-random prefix."""
        assert is_placeholder_password("replace-with-random-abc123") is True
        assert is_placeholder_password("REPLACE-WITH-RANDOM") is True

    def test_dev_secret_prefix(self):
        """Test detection of dev-secret prefix."""
        assert is_placeholder_password("dev-secret-key") is True
        assert is_placeholder_password("DEV-SECRET-KEY") is True

    def test_default_secret_prefix(self):
        """Test detection of default-secret prefix."""
        assert is_placeholder_password("default-secret-key") is True

    def test_change_me_in_production(self):
        """Test detection of change-me-in-production."""
        assert is_placeholder_password("change-me-in-production") is True

    def test_normal_password_not_placeholder(self):
        """Test that normal password is not detected as placeholder."""
        assert is_placeholder_password("my-normal-password") is False
        assert is_placeholder_password("ace-secret") is False  # forbidden, but not placeholder


class TestCheckDatabasePassword:
    """Tests for database password checking."""

    def test_empty_password_production_fails(self):
        """Test that empty password fails in production mode."""
        result = check_database_password(None, SecurityMode.PRODUCTION)
        assert result.status == "fail"
        assert "required" in result.message.lower()

    def test_empty_password_development_warning(self):
        """Test that empty password gives warning in development mode."""
        result = check_database_password(None, SecurityMode.DEVELOPMENT)
        assert result.status == "warning"

    def test_empty_password_pilot_warning(self):
        """Test that empty password gives warning in pilot mode."""
        result = check_database_password(None, SecurityMode.PILOT)
        assert result.status == "warning"

    def test_forbidden_password_production_fails(self):
        """Test that forbidden password fails in production mode."""
        result = check_database_password("ace-secret", SecurityMode.PRODUCTION)
        assert result.status == "fail"
        assert "weak default" in result.message.lower()

    def test_dev_default_password_production_fails(self):
        """Test that dev-password-change-in-production fails in production mode."""
        result = check_database_password(
            "dev-password-change-in-production", SecurityMode.PRODUCTION
        )
        assert result.status == "fail"
        assert "weak default" in result.message.lower()

    def test_dev_default_password_development_warning(self):
        """Test that dev-password-change-in-production gives warning in development mode."""
        result = check_database_password(
            "dev-password-change-in-production", SecurityMode.DEVELOPMENT
        )
        assert result.status == "warning"

    def test_forbidden_password_development_warning(self):
        """Test that forbidden password gives warning in development mode."""
        result = check_database_password("ace-secret", SecurityMode.DEVELOPMENT)
        assert result.status == "warning"

    def test_short_password_production_fails(self):
        """Test that short password fails in production mode."""
        result = check_database_password("short", SecurityMode.PRODUCTION)
        assert result.status == "fail"
        assert "too short" in result.message.lower()

    def test_nine_char_password_production_passes(self):
        """Test that 9+ char password passes in production mode."""
        result = check_database_password("ninechars!", SecurityMode.PRODUCTION)
        assert result.status == "pass"

    def test_strong_password_all_modes_pass(self):
        """Test that strong password passes in all modes."""
        strong_pwd = "my-strong-password-123!"
        for mode in [SecurityMode.DEVELOPMENT, SecurityMode.PILOT, SecurityMode.PRODUCTION]:
            result = check_database_password(strong_pwd, mode)
            assert result.status == "pass"


class TestCheckSecretKey:
    """Tests for secret key checking."""

    def test_empty_key_production_fails(self):
        """Test that empty key fails in production mode."""
        result = check_secret_key(None, SecurityMode.PRODUCTION)
        assert result.status == "fail"

    def test_empty_key_development_warning(self):
        """Test that empty key gives warning in development mode."""
        result = check_secret_key(None, SecurityMode.DEVELOPMENT)
        assert result.status == "warning"

    def test_placeholder_key_production_fails(self):
        """Test that placeholder key fails in production mode."""
        result = check_secret_key("dev-secret-key", SecurityMode.PRODUCTION)
        assert result.status == "fail"

    def test_strong_key_all_modes_pass(self):
        """Test that strong key passes in all modes."""
        strong_key = "a" * 64  # Strong random key
        for mode in [SecurityMode.DEVELOPMENT, SecurityMode.PILOT, SecurityMode.PRODUCTION]:
            result = check_secret_key(strong_key, mode)
            assert result.status == "pass"


class TestCheckEncryptionKey:
    """Tests for encryption key checking."""

    def test_empty_key_production_fails(self):
        """Test that empty encryption key fails in production mode."""
        result = check_encryption_key(None, SecurityMode.PRODUCTION)
        assert result.status == "fail"

    def test_empty_key_development_warning(self):
        """Test that empty encryption key gives warning in development mode."""
        result = check_encryption_key(None, SecurityMode.DEVELOPMENT)
        assert result.status == "warning"

    def test_placeholder_key_production_fails(self):
        """Test that placeholder encryption key fails in production mode."""
        result = check_encryption_key("replace-with-random-key", SecurityMode.PRODUCTION)
        assert result.status == "fail"


class TestCheckRootUser:
    """Tests for root user authorization checking."""

    def test_non_root_passes(self):
        """Test that non-root user passes."""
        result = check_root_user(False, False, False)
        assert result.status == "pass"

    def test_root_without_multi_user_fails(self):
        """Test that root without multi-user mode fails."""
        result = check_root_user(True, False, False)
        assert result.status == "fail"

    def test_root_without_allow_flag_fails(self):
        """Test that root without allow flag fails."""
        result = check_root_user(True, True, False)
        assert result.status == "fail"

    def test_root_with_both_flags_passes(self):
        """Test that root with both flags passes."""
        result = check_root_user(True, True, True)
        assert result.status == "pass"


class TestCheckAll:
    """Tests for check_all function."""

    def test_check_all_returns_dict(self, monkeypatch):
        """Test that check_all returns a dictionary."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)

        result = check_all()
        assert isinstance(result, dict)
        assert "status" in result
        assert "mode" in result

    def test_check_all_includes_all_checks(self, monkeypatch):
        """Test that check_all includes all check categories."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)

        result = check_all()
        assert "database_password" in result
        assert "secret_key" in result
        assert "encryption_key" in result
        assert "root_user" in result

    def test_check_all_production_mode_unhealthy_with_defaults(self, monkeypatch):
        """Test that production mode returns unhealthy with default values."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.setenv("DB_PASSWORD", "ace-secret")
        monkeypatch.setenv("SECRET_KEY", "")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "")
        # Simulate PostgreSQL backend (DB_PASSWORD matters only for PostgreSQL)
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: True)

        result = check_all()
        assert result["status"] == "unhealthy"
        assert result["mode"] == "production"

    def test_check_all_development_mode_warning_with_defaults(self, monkeypatch):
        """Test that development mode returns warning with default values."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)

        result = check_all()
        assert result["status"] in ["warning", "healthy"]  # Depends on auto-generation

    # Issue #2810: SQLite backend tests
    def test_check_all_sqlite_production_no_password_healthy(self, monkeypatch):
        """Test that production + SQLite + no DB_PASSWORD returns healthy (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate SQLite backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

        result = check_all()
        assert result["status"] == "healthy"
        assert result["database_password"]["status"] == "not_applicable"
        assert result["database_password"]["backend"] == "sqlite"
        assert result["database_password"]["applicable"] is False
        assert "sqlite" in result["database_password"]["message"].lower()

    def test_check_all_sqlite_production_with_password_ignored(self, monkeypatch):
        """Test that SQLite ignores DB_PASSWORD even if set (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.setenv("DB_PASSWORD", "ace-secret")  # Weak/forbidden password
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate SQLite backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

        result = check_all()
        assert result["status"] == "healthy"
        assert result["database_password"]["status"] == "not_applicable"
        assert result["database_password"]["backend"] == "sqlite"
        assert result["database_password"]["applicable"] is False

    def test_check_all_sqlite_development_no_password(self, monkeypatch):
        """Test that development + SQLite returns not_applicable (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
        # Simulate SQLite backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

        result = check_all()
        assert result["database_password"]["status"] == "not_applicable"
        assert result["database_password"]["backend"] == "sqlite"
        assert result["database_password"]["applicable"] is False

    # Issue #2810: PostgreSQL backend tests
    def test_check_all_postgresql_production_no_password_fails(self, monkeypatch):
        """Test that production + PostgreSQL + no DB_PASSWORD returns unhealthy (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate PostgreSQL backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: True)

        result = check_all()
        assert result["status"] == "unhealthy"
        assert result["database_password"]["status"] == "fail"
        assert result["database_password"]["backend"] == "postgresql"
        assert result["database_password"]["applicable"] is True

    def test_check_all_postgresql_production_weak_password_fails(self, monkeypatch):
        """Test that production + PostgreSQL + weak DB_PASSWORD returns unhealthy (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.setenv("DB_PASSWORD", "ace-secret")
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate PostgreSQL backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: True)

        result = check_all()
        assert result["status"] == "unhealthy"
        assert result["database_password"]["status"] == "fail"
        assert result["database_password"]["backend"] == "postgresql"
        assert result["database_password"]["applicable"] is True

    def test_check_all_postgresql_production_strong_password_passes(self, monkeypatch):
        """Test that production + PostgreSQL + strong DB_PASSWORD passes (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.setenv("DB_PASSWORD", "my-strong-password-123!")
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate PostgreSQL backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: True)

        result = check_all()
        assert result["status"] == "healthy"
        assert result["database_password"]["status"] == "pass"
        assert result["database_password"]["backend"] == "postgresql"
        assert result["database_password"]["applicable"] is True

    def test_check_all_not_applicable_does_not_affect_overall_status(self, monkeypatch):
        """Test that not_applicable status does not make overall status unhealthy (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate SQLite backend
        monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)

        result = check_all()
        # not_applicable should not cause unhealthy
        assert result["status"] == "healthy"
        # Verify no "fail" in effective statuses
        assert result["database_password"]["status"] == "not_applicable"

    def test_check_all_backend_detection_failure_fail_closed(self, monkeypatch):
        """Test that backend detection failure defaults to PostgreSQL (fail-closed) (Issue #2810)."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("SECRET_KEY", "a-strong-random-secret-key-for-prod-1234567890")
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "a-strong-encryption-key-1234567890ab")
        # Simulate backend detection raising an exception -> should default to PostgreSQL
        # (fail-closed: if we can't determine the backend, assume password is needed)

        def _raise_error():
            raise RuntimeError("Database not available")

        monkeypatch.setattr("app.repositories.database.is_postgresql", _raise_error)

        result = check_all()
        # Should fall back to PostgreSQL behavior (require password)
        assert result["database_password"]["status"] == "fail"
        assert result["database_password"]["backend"] == "postgresql"
        assert result["database_password"]["applicable"] is True


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_check_result_creation(self):
        """Test CheckResult creation with all fields."""
        result = CheckResult(
            status="pass", message="Test message", recommendation="Test recommendation"
        )
        assert result.status == "pass"
        assert result.message == "Test message"
        assert result.recommendation == "Test recommendation"

    def test_check_result_without_recommendation(self):
        """Test CheckResult creation without recommendation."""
        result = CheckResult(status="pass", message="Test message")
        assert result.recommendation is None
