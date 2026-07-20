#!/usr/bin/env python3
"""Test Redis authentication fail-closed behavior (Issue #1895)."""

from __future__ import annotations

import pytest

from app.utils.security_env import get_redis_password


class TestRedisAuthFailClosed:
    """Test Redis password validation in production vs development."""

    def test_empty_password_rejected_in_production(self, monkeypatch):
        """Production environment must reject empty password."""
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="REDIS_PASSWORD must be set in production"):
            get_redis_password()

    def test_weak_password_rejected_in_production(self, monkeypatch):
        """Production environment must reject weak password."""
        monkeypatch.setenv("REDIS_PASSWORD", "replace-with-random-redis-password")
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="REDIS_PASSWORD must be strong in production"):
            get_redis_password()

    def test_dev_allows_empty_password(self, monkeypatch):
        """Development environment allows empty password (returns None)."""
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.setenv("FLASK_ENV", "development")

        assert get_redis_password() is None

    def test_dev_allows_strong_password(self, monkeypatch):
        """Development environment accepts strong password."""
        monkeypatch.setenv("REDIS_PASSWORD", "a-strong-random-redis-password-12345678")
        monkeypatch.setenv("FLASK_ENV", "development")

        assert get_redis_password() == "a-strong-random-redis-password-12345678"

    def test_placeholder_detected_in_production(self, monkeypatch):
        """Production environment must detect replace-with-random placeholder."""
        monkeypatch.setenv("REDIS_PASSWORD", "replace-with-random-redis-pass")
        monkeypatch.setenv("FLASK_ENV", "production")

        with pytest.raises(RuntimeError, match="REDIS_PASSWORD must be strong"):
            get_redis_password()

    def test_strong_password_accepted_in_production(self, monkeypatch):
        """Production environment accepts strong password."""
        monkeypatch.setenv("REDIS_PASSWORD", "a-strong-random-redis-password-for-production-xyz")
        monkeypatch.setenv("FLASK_ENV", "production")

        assert get_redis_password() == "a-strong-random-redis-password-for-production-xyz"


class TestCacheManagerAuthBehavior:
    """Test CacheManager authentication error handling."""

    def test_auth_failure_raises_not_fallback(self, monkeypatch):
        """Authentication failure should raise RuntimeError, not fallback to memory cache."""
        monkeypatch.setenv("REDIS_PASSWORD", "wrong-password")
        monkeypatch.setenv("CACHE_BACKEND", "redis")
        monkeypatch.setenv("FLASK_ENV", "production")

        # Mock _get_client to raise authentication error directly
        from unittest.mock import patch

        def mock_get_client():
            raise RuntimeError(
                "Redis authentication failed: NOAUTH Authentication required. "
                "Check REDIS_PASSWORD."
            )

        # Reset singleton instance to allow re-initialization
        from app.utils.cache import CacheManager
        CacheManager._instance = None

        with patch("app.utils.cache.RedisCache._get_client", side_effect=mock_get_client):
            with pytest.raises(RuntimeError, match="Redis authentication failed"):
                CacheManager(backend="redis")


class TestBackupScriptRedisAuth:
    """Test backup script Redis authentication support."""

    def test_backup_script_has_redis_password_variable(self):
        """Backup script should read REDIS_PASSWORD environment variable."""
        import yaml

        with open("k8s/extras/backup/backup-script-configmap.yaml") as f:
            config = yaml.safe_load(f)

        backup_script = config["data"]["backup.sh"]

        # Should reference REDIS_PASSWORD
        assert "REDIS_PASSWORD" in backup_script
        # Should use REDISCLI_AUTH for authentication
        assert "REDISCLI_AUTH" in backup_script

    def test_backup_cronjob_has_redis_password_env(self):
        """Backup CronJob should inject REDIS_PASSWORD environment variable."""
        import yaml

        with open("k8s/extras/backup/cronjob.yaml") as f:
            config = yaml.safe_load(f)

        # Find REDIS_PASSWORD in env vars
        containers = config["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        env_vars = containers[0]["env"]

        redis_password_found = False
        for env in env_vars:
            if env.get("name") == "REDIS_PASSWORD":
                redis_password_found = True
                assert env["valueFrom"]["secretKeyRef"]["key"] == "REDIS_PASSWORD"
                assert env["valueFrom"]["secretKeyRef"]["name"] == "open-ace-secrets"
                assert env["valueFrom"]["secretKeyRef"].get("optional", False) is True
                break

        assert redis_password_found, (
            "REDIS_PASSWORD environment variable not found in backup cronjob"
        )
