"""
Tests for security_env module.

Tests key validation, strict mode, and secret strength checks.
Updated for Issue #2185: Unified security mode definition.
"""

import os
from unittest.mock import patch

import pytest

from app.utils.security_env import (
    get_encryption_key_material,
    get_redis_password,
    get_secret_key_for_app,
    get_upload_auth_key,
    is_strict_mode,
    is_weak_secret_value,
    validate_secret_strength,
)
from app.utils.security_mode import SecurityMode, reset_security_mode_cache


class TestIsStrictMode:
    """Tests for is_strict_mode() function."""

    def test_strict_mode_in_production(self):
        """Test that strict mode is enabled in production."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "production"}, clear=False):
            assert is_strict_mode() is True

    def test_non_strict_mode_in_development(self):
        """Test that strict mode is disabled in development."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            assert is_strict_mode() is False

    def test_non_strict_mode_in_pilot(self):
        """Test that strict mode is disabled in pilot mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "pilot"}, clear=False):
            assert is_strict_mode() is False

    def test_flask_env_backward_compat_production(self):
        """Test FLASK_ENV=production still enables strict mode (backward compat)."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            # Remove OPENACE_SECURITY_MODE if set
            os.environ.pop("OPENACE_SECURITY_MODE", None)
            assert is_strict_mode() is True


class TestIsWeakSecretValue:
    """Tests for is_weak_secret_value() function."""

    def test_none_is_weak(self):
        """Test that None is considered weak."""
        assert is_weak_secret_value(None) is True

    def test_empty_string_is_weak(self):
        """Test that empty string is weak."""
        assert is_weak_secret_value("") is True

    def test_placeholder_is_weak(self):
        """Test that known placeholders are weak."""
        assert is_weak_secret_value("change-me-in-production") is True
        assert is_weak_secret_value("dev-secret-key") is True

    def test_prefix_placeholder_is_weak(self):
        """Test that placeholders with prefix are weak."""
        assert is_weak_secret_value("replace-with-random-key") is True
        assert is_weak_secret_value("replace-with-random-123") is True

    def test_strong_value_is_not_weak(self):
        """Test that strong values are not weak."""
        assert is_weak_secret_value("a-very-long-and-secure-key-12345") is False


class TestValidateSecretStrength:
    """Tests for validate_secret_strength() function."""

    def test_validates_strong_key(self):
        """Test that a strong key passes validation."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise
            validate_secret_strength(
                "a-very-long-and-secure-secret-key-32-chars",
                "TEST_KEY",
                min_length=32,
            )

    def test_raises_on_empty_in_production(self):
        """Test that empty key raises in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be set"):
                validate_secret_strength(None, "TEST_KEY")

    def test_raises_on_weak_value_in_production(self):
        """Test that weak value raises in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be set to a strong"):
                validate_secret_strength("dev-secret-key", "TEST_KEY")

    def test_raises_on_short_value_in_production(self):
        """Test that short value raises in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="at least 32 characters"):
                validate_secret_strength("too-short", "TEST_KEY", min_length=32)

    def test_warns_on_empty_in_development(self):
        """Test that empty key logs warning in development mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise
            validate_secret_strength(None, "TEST_KEY")

    def test_warns_on_weak_value_in_development(self):
        """Test that weak value logs warning in development mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise
            validate_secret_strength("dev-secret-key", "TEST_KEY")

    def test_warns_on_short_value_in_development(self):
        """Test that short value logs warning in development mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise
            validate_secret_strength("too-short", "TEST_KEY", min_length=32)


class TestGetSecretKeyForApp:
    """Tests for get_secret_key_for_app() function."""

    def test_raises_when_not_set_in_development(self):
        """Test that development mode raises without key (requires explicit or auto-generated)."""
        reset_security_mode_cache()
        # Clear SECRET_KEY to test "not set" scenario
        env = {"OPENACE_SECURITY_MODE": "development", "SECRET_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="SECRET_KEY not set"):
                get_secret_key_for_app()

    def test_raises_in_production_without_key(self):
        """Test that production raises without key."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production", "SECRET_KEY": ""},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be set"):
                get_secret_key_for_app()

    def test_returns_provided_key(self):
        """Test that provided key is returned."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            key = get_secret_key_for_app("my-custom-secret-key-32-chars-long")
            assert key == "my-custom-secret-key-32-chars-long"

    def test_validates_key_strength_in_production(self):
        """Test that key strength is validated in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {
                "OPENACE_SECURITY_MODE": "production",
                "SECRET_KEY": "too-short",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="at least 32 characters"):
                get_secret_key_for_app()


class TestGetEncryptionKeyMaterial:
    """Tests for get_encryption_key_material() function."""

    def test_raises_when_not_set_in_development(self):
        """Test that development mode raises without key (requires explicit or auto-generated)."""
        reset_security_mode_cache()
        # Clear OPENACE_ENCRYPTION_KEY to test "not set" scenario
        env = {"OPENACE_SECURITY_MODE": "development", "OPENACE_ENCRYPTION_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="OPENACE_ENCRYPTION_KEY not set"):
                get_encryption_key_material(purpose="test")

    def test_raises_in_production_without_key(self):
        """Test that production raises without key."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production", "OPENACE_ENCRYPTION_KEY": ""},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be set"):
                get_encryption_key_material(purpose="test")

    def test_validates_key_strength_in_production(self):
        """Test that key strength is validated in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {
                "OPENACE_SECURITY_MODE": "production",
                "OPENACE_ENCRYPTION_KEY": "too-short",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="at least 32 characters"):
                get_encryption_key_material(purpose="test")


class TestGetUploadAuthKey:
    """Tests for get_upload_auth_key() function."""

    def test_returns_none_when_not_set(self):
        """Test that None is returned when key is not set."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ, {"OPENACE_SECURITY_MODE": "development", "UPLOAD_AUTH_KEY": ""}, clear=False
        ):
            key = get_upload_auth_key()
            assert key is None

    def test_returns_key_when_set_and_strong(self):
        """Test that key is returned when set and strong."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {
                "OPENACE_SECURITY_MODE": "development",
                "UPLOAD_AUTH_KEY": "my-upload-key-32-chars-long",
            },
            clear=False,
        ):
            key = get_upload_auth_key()
            assert key == "my-upload-key-32-chars-long"

    def test_returns_none_for_weak_key(self):
        """Test that None is returned for weak key."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "development", "UPLOAD_AUTH_KEY": "dev-secret-key"},
            clear=False,
        ):
            key = get_upload_auth_key()
            assert key is None


class TestGetRedisPassword:
    """Tests for get_redis_password() function."""

    def test_raises_when_not_set_in_development(self):
        """Test that development mode raises without password (requires explicit or auto-generated)."""
        reset_security_mode_cache()
        # Clear REDIS_PASSWORD to test "not set" scenario
        env = {"OPENACE_SECURITY_MODE": "development", "REDIS_PASSWORD": ""}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="REDIS_PASSWORD not set"):
                get_redis_password()

    def test_raises_in_production_without_password(self):
        """Test that production raises without password."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {"OPENACE_SECURITY_MODE": "production", "REDIS_PASSWORD": ""},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="must be set"):
                get_redis_password()

    def test_validates_password_strength_in_production(self):
        """Test that password strength is validated in production mode."""
        reset_security_mode_cache()
        with patch.dict(
            os.environ,
            {
                "OPENACE_SECURITY_MODE": "production",
                "REDIS_PASSWORD": "too-short",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="at least 24 characters"):
                get_redis_password()
