from __future__ import annotations

import pytest

from app.utils.security_env import (
    get_encryption_key_material,
    get_secret_key_for_app,
    get_upload_auth_key,
)
from app.utils.security_mode import reset_security_mode_cache


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
