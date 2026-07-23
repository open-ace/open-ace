"""Tests for Issue #1895: Redis authentication configuration.

This module verifies:
- Redis startup command includes --requirepass
- Redis password environment variables are properly configured
- Health check does not expose password in plaintext
- Secret has non-empty placeholder for REDIS_PASSWORD
- Secret reference consistency across files
- Redis password fail-closed behavior in security_env.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


class TestRedisAuthenticationEnabled:
    """Test that Redis is properly configured with authentication."""

    def test_redis_startup_command_includes_requirepass(self):
        """Redis startup command should include --requirepass."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(database_content))

        # Find Redis StatefulSet
        redis_sts = None
        for doc in documents:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found in database.yaml"

        # Get the command from the container spec
        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert len(containers) > 0, "No containers found in Redis StatefulSet"

        redis_container = containers[0]
        command = redis_container.get("command", [])

        # Command should be a shell wrapper with --requirepass
        # Format: ["/bin/sh", "-c", "redis-server ... --requirepass ..."]
        command_str = " ".join(str(c) for c in command)

        assert "--requirepass" in command_str, (
            "Redis startup command should include --requirepass. "
            f"Command: {command_str}"
        )

    def test_redis_password_env_from_secret(self):
        """Redis should have REDIS_PASSWORD env from secret reference."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(database_content))

        redis_sts = None
        for doc in documents:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found in database.yaml"

        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        redis_container = containers[0]
        env_vars = redis_container.get("env", [])

        # Find REDIS_PASSWORD env var
        redis_password_env = None
        for env_var in env_vars:
            if env_var.get("name") == "REDIS_PASSWORD":
                redis_password_env = env_var
                break

        assert redis_password_env is not None, "REDIS_PASSWORD env var not found in Redis container"

        # Verify it references the secret
        secret_key_ref = redis_password_env.get("valueFrom", {}).get("secretKeyRef", {})
        assert secret_key_ref.get("name") == "open-ace-secrets", (
            "REDIS_PASSWORD should reference 'open-ace-secrets' secret"
        )
        assert secret_key_ref.get("key") == "REDIS_PASSWORD", (
            "REDIS_PASSWORD should reference 'REDIS_PASSWORD' key"
        )

    def test_rediscli_auth_password_env_from_secret(self):
        """Redis should have REDISCLI_AUTH_PASSWORD env from secret reference."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(database_content))

        redis_sts = None
        for doc in documents:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found in database.yaml"

        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        redis_container = containers[0]
        env_vars = redis_container.get("env", [])

        # Find REDISCLI_AUTH_PASSWORD env var
        auth_env = None
        for env_var in env_vars:
            if env_var.get("name") == "REDISCLI_AUTH_PASSWORD":
                auth_env = env_var
                break

        assert auth_env is not None, "REDISCLI_AUTH_PASSWORD env var not found in Redis container"

        # Verify it references the secret
        secret_key_ref = auth_env.get("valueFrom", {}).get("secretKeyRef", {})
        assert secret_key_ref.get("name") == "open-ace-secrets", (
            "REDISCLI_AUTH_PASSWORD should reference 'open-ace-secrets' secret"
        )
        assert secret_key_ref.get("key") == "REDIS_PASSWORD", (
            "REDISCLI_AUTH_PASSWORD should reference 'REDIS_PASSWORD' key"
        )


class TestRedisHealthCheckWithAuth:
    """Test that Redis health checks do not expose password in plaintext."""

    def test_liveness_probe_uses_redis_cli_without_plaintext_password(self):
        """Liveness probe should use redis-cli without -a flag."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(database_content))

        redis_sts = None
        for doc in documents:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found in database.yaml"

        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        redis_container = containers[0]
        liveness_probe = redis_container.get("livenessProbe", {})

        # Get the probe command
        probe_command = liveness_probe.get("exec", {}).get("command", [])
        probe_str = " ".join(str(c) for c in probe_command)

        # Should not have -a flag with password
        assert "-a" not in probe_str or "REDIS_PASSWORD" not in probe_str, (
            "Liveness probe should not pass password via -a flag. "
            "Use REDISCLI_AUTH_PASSWORD env variable instead."
        )

    def test_readiness_probe_uses_redis_cli_without_plaintext_password(self):
        """Readiness probe should use redis-cli without -a flag."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(database_content))

        redis_sts = None
        for doc in documents:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found in database.yaml"

        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        redis_container = containers[0]
        readiness_probe = redis_container.get("readinessProbe", {})

        # Get the probe command
        probe_command = readiness_probe.get("exec", {}).get("command", [])
        probe_str = " ".join(str(c) for c in probe_command)

        # Should not have -a flag with password
        assert "-a" not in probe_str or "REDIS_PASSWORD" not in probe_str, (
            "Readiness probe should not pass password via -a flag. "
            "Use REDISCLI_AUTH_PASSWORD env variable instead."
        )


class TestSecretReferenceConsistency:
    """Test that Secret key names are consistent across files."""

    def test_redis_password_secret_key_name_matches_reference(self):
        """REDIS_PASSWORD key in Secret should match secretKeyRef in database.yaml."""
        # Read Secret from configmap.yaml
        configmap_path = ROOT / "k8s" / "configmap.yaml"
        configmap_content = configmap_path.read_text(encoding="utf-8")
        configmap_docs = list(yaml.safe_load_all(configmap_content))

        secret = None
        for doc in configmap_docs:
            if doc and doc.get("kind") == "Secret":
                secret = doc
                break

        assert secret is not None, "Secret not found in configmap.yaml"

        # Get the keys in stringData
        string_data = secret.get("stringData", {})
        secret_keys = set(string_data.keys())

        # Verify REDIS_PASSWORD exists
        assert "REDIS_PASSWORD" in secret_keys, (
            f"REDIS_PASSWORD key not found in Secret. Keys: {secret_keys}"
        )

        # Verify it's not empty (should have placeholder)
        redis_password_value = string_data.get("REDIS_PASSWORD", "")
        assert redis_password_value != "", (
            "REDIS_PASSWORD should not be empty in Secret"
        )

    def test_database_yaml_secret_key_ref_targets_correct_secret(self):
        """Secret reference in database.yaml should point to correct secret and key."""
        database_path = ROOT / "k8s" / "database.yaml"
        database_content = database_path.read_text(encoding="utf-8")
        database_docs = list(yaml.safe_load_all(database_content))

        # Find Redis StatefulSet
        redis_sts = None
        for doc in database_docs:
            if doc and doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found"

        containers = redis_sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        redis_container = containers[0]
        env_vars = redis_container.get("env", [])

        # Check REDIS_PASSWORD reference
        for env_var in env_vars:
            if env_var.get("name") == "REDIS_PASSWORD":
                secret_key_ref = env_var.get("valueFrom", {}).get("secretKeyRef", {})
                assert secret_key_ref.get("name") == "open-ace-secrets"
                assert secret_key_ref.get("key") == "REDIS_PASSWORD"
                break


class TestRedisPasswordFailClosed:
    """Test that get_redis_password fails closed in production."""

    def test_empty_redis_password_detected_as_weak(self):
        """Empty string should be detected as weak secret."""
        from app.utils.security_env import is_weak_secret_value

        assert is_weak_secret_value("") is True
        assert is_weak_secret_value(None) is True

    def test_production_startup_rejects_empty_redis_password(self, monkeypatch):
        """In production, empty REDIS_PASSWORD should raise RuntimeError."""
        from app.utils.security_env import get_redis_password

        # Set production environment
        monkeypatch.setenv("FLASK_ENV", "production")
        # Set empty password
        monkeypatch.setenv("REDIS_PASSWORD", "")

        with pytest.raises(RuntimeError, match="REDIS_PASSWORD must be set"):
            get_redis_password()

    def test_production_startup_rejects_placeholder_redis_password(self, monkeypatch):
        """In production, placeholder REDIS_PASSWORD should raise RuntimeError."""
        from app.utils.security_env import get_redis_password

        # Set production environment
        monkeypatch.setenv("FLASK_ENV", "production")
        # Set placeholder password
        monkeypatch.setenv("REDIS_PASSWORD", "replace-with-random-redis-password")

        with pytest.raises(RuntimeError, match="REDIS_PASSWORD must be set"):
            get_redis_password()

    def test_development_allows_empty_redis_password_with_warning(self, monkeypatch):
        """In development, empty REDIS_PASSWORD should return dev password with warning."""
        from app.utils.security_env import get_redis_password

        # Set development environment (default)
        monkeypatch.setenv("FLASK_ENV", "development")
        # Unset password
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)

        # Should return dev password without raising
        password = get_redis_password()
        assert password == "dev-redis-password"

    def test_development_accepts_strong_redis_password(self, monkeypatch):
        """Strong password should be accepted in development."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.setenv("REDIS_PASSWORD", "a-strong-64-char-random-password-12345678901234567890")

        password = get_redis_password()
        assert password == "a-strong-64-char-random-password-12345678901234567890"

    def test_production_accepts_strong_redis_password(self, monkeypatch):
        """Strong password should be accepted in production."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("REDIS_PASSWORD", "a-strong-64-char-random-password-12345678901234567890")

        password = get_redis_password()
        assert password == "a-strong-64-char-random-password-12345678901234567890"


class TestRedisPasswordSpecialCharacters:
    """Test that passwords with special characters are handled correctly."""

    def test_password_with_dollar_sign_handled_correctly(self, monkeypatch):
        """Password containing $ should be accepted."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("REDIS_PASSWORD", "pass$word$123")

        password = get_redis_password()
        assert password == "pass$word$123"

    def test_password_with_exclamation_handled_correctly(self, monkeypatch):
        """Password containing ! should be accepted."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("REDIS_PASSWORD", "pass!word!123")

        password = get_redis_password()
        assert password == "pass!word!123"

    def test_password_with_backtick_handled_correctly(self, monkeypatch):
        """Password containing backtick should be accepted."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("REDIS_PASSWORD", "pass`word`123")

        password = get_redis_password()
        assert password == "pass`word`123"

    def test_password_with_special_chars_handled_correctly(self, monkeypatch):
        """Password containing multiple special characters should be accepted."""
        from app.utils.security_env import get_redis_password

        monkeypatch.setenv("FLASK_ENV", "production")
        # Mix of special characters
        monkeypatch.setenv("REDIS_PASSWORD", "P@$$w0rd!#$%^&*()_+-=[]{}|;':\",./<>?")

        password = get_redis_password()
        assert password == "P@$$w0rd!#$%^&*()_+-=[]{}|;':\",./<>?"
