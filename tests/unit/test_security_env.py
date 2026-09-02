from __future__ import annotations

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
from app.utils.security_mode import reset_security_mode_cache

pytestmark = [
    pytest.mark.regression,
    pytest.mark.security,
    pytest.mark.issue(2185),
]


class TestSecretKeyValidation:
    def test_missing_secret_key_raises_in_development(self, monkeypatch):
        """Development mode now requires explicit or auto-generated key (Issue #2185)."""
        reset_security_mode_cache()
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        with pytest.raises(RuntimeError, match="SECRET_KEY not set"):
            get_secret_key_for_app()

    def test_missing_secret_key_raises_in_production(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        with pytest.raises(RuntimeError, match="SECRET_KEY environment variable must be set"):
            get_secret_key_for_app()

    def test_weak_secret_key_raises_in_production(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong, unique value"):
            get_secret_key_for_app()

    def test_replace_with_random_secret_key_raises_in_production(self, monkeypatch):
        """Production environment must reject replace-with-random-* placeholders."""
        reset_security_mode_cache()
        monkeypatch.setenv("SECRET_KEY", "replace-with-random-flask-secret")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong, unique value"):
            get_secret_key_for_app()

    def test_explicit_config_secret_key_is_respected(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        assert get_secret_key_for_app("config-secret-key") == "config-secret-key"


class TestEncryptionKeyValidation:
    def test_encryption_key_uses_explicit_env(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "my-strong-encryption-key-32-chars")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        assert (
            get_encryption_key_material(purpose="API key encryption")
            == "my-strong-encryption-key-32-chars"
        )

    def test_missing_encryption_key_raises_in_development(self, monkeypatch):
        """Development mode now requires explicit or auto-generated key (Issue #2185)."""
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "some-other-secret-key")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        with pytest.raises(RuntimeError, match="OPENACE_ENCRYPTION_KEY not set"):
            get_encryption_key_material(purpose="API key encryption")

    def test_missing_encryption_key_raises_in_production(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "strong-secret-key")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        with pytest.raises(RuntimeError, match="OPENACE_ENCRYPTION_KEY must be set"):
            get_encryption_key_material(purpose="SMTP password encryption")

    def test_replace_with_random_encryption_key_raises_in_production(self, monkeypatch):
        """Production environment must reject replace-with-random-* placeholders for encryption key."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "replace-with-random-dedicated-encryption-key")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        with pytest.raises(RuntimeError, match="OPENACE_ENCRYPTION_KEY must be set"):
            get_encryption_key_material(purpose="test")


class TestUploadAuthValidation:
    def test_missing_upload_auth_key_disables_uploads(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.delenv("UPLOAD_AUTH_KEY", raising=False)
        assert get_upload_auth_key() is None

    def test_weak_upload_auth_key_is_rejected(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "change-me-in-production")
        assert get_upload_auth_key() is None

    def test_replace_with_random_upload_auth_key_is_rejected(self, monkeypatch):
        """Upload endpoint must be disabled when using replace-with-random-* placeholder."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "replace-with-random-upload-auth-key")
        assert get_upload_auth_key() is None

    def test_strong_upload_auth_key_is_returned(self, monkeypatch):
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "upload-auth-key-at-least-32-chars-long")
        assert get_upload_auth_key() == "upload-auth-key-at-least-32-chars-long"


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
            # Should not raise; returns None on success
            result = validate_secret_strength(
                "a-very-long-and-secure-secret-key-32-chars",
                "TEST_KEY",
                min_length=32,
            )
            assert result is None

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
            # Should not raise; returns None after logging warning
            result = validate_secret_strength(None, "TEST_KEY")
            assert result is None

    def test_warns_on_weak_value_in_development(self):
        """Test that weak value logs warning in development mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise; returns None after logging warning
            result = validate_secret_strength("dev-secret-key", "TEST_KEY")
            assert result is None

    def test_warns_on_short_value_in_development(self):
        """Test that short value logs warning in development mode."""
        reset_security_mode_cache()
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            # Should not raise; returns None after logging warning
            result = validate_secret_strength("too-short", "TEST_KEY", min_length=32)
            assert result is None


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
