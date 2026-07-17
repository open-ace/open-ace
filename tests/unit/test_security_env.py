from __future__ import annotations

import pytest

from app.utils.security_env import (
    get_encryption_key_material,
    get_secret_key_for_app,
    get_upload_auth_key,
)


class TestSecretKeyValidation:
    def test_missing_secret_key_uses_dev_fallback_outside_production(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("FLASK_ENV", "development")

        assert get_secret_key_for_app() == "dev-secret-key"

    def test_missing_secret_key_raises_in_production(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="SECRET_KEY environment variable must be set"):
            get_secret_key_for_app()

    def test_weak_secret_key_raises_in_production(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong, unique value"):
            get_secret_key_for_app()

    def test_explicit_config_secret_key_is_respected(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("FLASK_ENV", "development")

        assert get_secret_key_for_app("config-secret-key") == "config-secret-key"


class TestEncryptionKeyValidation:
    def test_encryption_key_uses_explicit_env(self, monkeypatch):
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "my-strong-encryption-key")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("FLASK_ENV", "development")

        assert (
            get_encryption_key_material(purpose="API key encryption") == "my-strong-encryption-key"
        )

    def test_encryption_key_no_longer_falls_back_to_secret_key(self, monkeypatch):
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "some-other-secret-key")
        monkeypatch.setenv("FLASK_ENV", "development")

        assert (
            get_encryption_key_material(purpose="API key encryption")
            == "openace-dev-encryption-key"
        )

    def test_missing_encryption_key_raises_in_production(self, monkeypatch):
        monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "strong-secret-key")
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="OPENACE_ENCRYPTION_KEY must be set"):
            get_encryption_key_material(purpose="SMTP password encryption")


class TestUploadAuthValidation:
    def test_missing_upload_auth_key_disables_uploads(self, monkeypatch):
        monkeypatch.delenv("UPLOAD_AUTH_KEY", raising=False)
        assert get_upload_auth_key() is None

    def test_weak_upload_auth_key_is_rejected(self, monkeypatch):
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "change-me-in-production")
        assert get_upload_auth_key() is None

    def test_strong_upload_auth_key_is_returned(self, monkeypatch):
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "upload-auth-key-123")
        assert get_upload_auth_key() == "upload-auth-key-123"
