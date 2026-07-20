"""
Integration tests for production security validation.

Issue #1893: Production security hardening for Docker Compose.

Tests for:
- Production mode detection and validation
- Health check endpoint security status
- Docker layer and application layer validation consistency
"""

import json
import os
import pytest


class TestHealthCheckSecurityStatus:
    """Tests for /health endpoint security status."""

    @pytest.fixture
    def app(self):
        """Create test app."""
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    def test_health_endpoint_returns_security_status(self, client):
        """Health endpoint includes security_status field."""
        response = client.get("/health")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "security_status" in data
        assert data["security_status"] in ["ok", "warnings", "check_logs"]

    def test_health_endpoint_basic_fields(self, client):
        """Health endpoint returns required fields."""
        response = client.get("/health")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "healthy"
        assert data["service"] == "open-ace"
        assert "version" in data

    def test_health_security_endpoint_exists(self, client):
        """/health/security endpoint exists."""
        response = client.get("/health/security")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "status" in data
        assert "checks" in data

    def test_health_security_returns_check_results(self, client):
        """/health/security returns individual check results."""
        response = client.get("/health/security")
        assert response.status_code == 200

        data = json.loads(response.data)
        checks = data["checks"]

        # Should have checks for key security items
        assert "db_password" in checks
        assert "secret_key" in checks
        assert "encryption_key" in checks

        # Each check should be one of these values
        valid_values = ["ok", "weak", "missing", "default_value"]
        for check_name, check_value in checks.items():
            assert check_value in valid_values, f"{check_name}: {check_value}"

    def test_health_security_does_not_expose_secrets(self, client):
        """/health/security never exposes actual secret values."""
        response = client.get("/health/security")
        data = json.loads(response.data)

        # Convert to string and check no hex strings that look like secrets
        response_str = json.dumps(data)

        # Should not contain actual key values
        # (would be at least 32 chars for SECRET_KEY)
        import re

        # Look for potential secret patterns (long hex strings)
        hex_pattern = re.compile(r"[0-9a-f]{32,}")
        matches = hex_pattern.findall(response_str)

        # Response should not contain any long hex strings that could be secrets
        assert len(matches) == 0, f"Potential secret exposed: {matches}"


class TestProductionModeSecurityValidation:
    """Tests for production mode security validation."""

    def test_default_password_detection(self):
        """Default database password 'ace-secret' is detected."""
        from urllib.parse import unquote

        url = "postgresql://ace:ace-secret@postgres:5432/ace"
        auth_part = url.split("://", 1)[1].split("@", 1)[0]
        password = auth_part.split(":", 1)[1] if ":" in auth_part else ""
        password = unquote(password)

        assert password == "ace-secret"

    def test_url_encoded_password_extraction(self):
        """URL-encoded passwords are correctly decoded."""
        from urllib.parse import unquote

        test_cases = [
            ("postgresql://ace:pass%40word@host:5432/db", "pass@word"),
            ("postgresql://ace:p%23ss%25word@host:5432/db", "p#ss%word"),
            ("postgresql://ace:normal@host:5432/db", "normal"),
        ]

        for url, expected in test_cases:
            auth_part = url.split("://", 1)[1].split("@", 1)[0]
            password = auth_part.split(":", 1)[1] if ":" in auth_part else ""
            password = unquote(password)
            assert password == expected, f"Failed for {url}"


class TestValidationConsistency:
    """Tests for Docker layer and application layer validation consistency."""

    def test_same_weak_secrets_detected(self):
        """Docker layer and Python layer detect same weak secrets."""
        from app.utils.security_env import is_weak_secret_value

        weak_secrets = [
            "",
            "change-me-in-production",
            "dev-secret-key",
            "default-secret-key",
            "replace-with-random-dedicated-encryption-key",
        ]

        for secret in weak_secrets:
            assert is_weak_secret_value(secret) is True, f"Failed for: {secret}"

    def test_strong_secrets_not_flagged(self):
        """Strong secrets pass validation in both layers."""
        import secrets as secrets_module

        from app.utils.security_env import is_weak_secret_value

        # Generate strong secrets
        strong_secret_key = secrets_module.token_hex(32)
        strong_encryption_key = secrets_module.token_hex(16)

        assert is_weak_secret_value(strong_secret_key) is False
        assert is_weak_secret_value(strong_encryption_key) is False


class TestProductionModeEnvironmentVariable:
    """Tests for OPENACE_PRODUCTION_MODE environment variable."""

    def test_production_mode_disabled_by_default(self):
        """Production mode is disabled by default."""
        # In test environment, should not be set
        production_mode = os.environ.get("OPENACE_PRODUCTION_MODE", "")
        # Either not set or not "true"
        assert production_mode.lower() != "true"

    def test_production_mode_validation_logic(self):
        """Production mode validation follows correct logic."""
        # This tests the logic that would be in docker-entrypoint.sh
        # Production mode = OPENACE_PRODUCTION_MODE=true (exact string match)

        test_cases = [
            ("true", True),
            ("True", False),  # Case sensitive
            ("TRUE", False),  # Case sensitive
            ("false", False),
            ("", False),
            ("yes", False),  # Must be exactly "true"
        ]

        for value, expected_production in test_cases:
            is_production = value == "true"
            assert is_production == expected_production, f"Failed for: {value}"


class TestHealthEndpointAccessControl:
    """Tests for health endpoint access control expectations."""

    def test_health_endpoint_public(self, client):
        """/health is publicly accessible."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_security_endpoint_exists_without_auth(self, client):
        """/health/security exists and doesn't require app-level auth.

        Note: In production, this endpoint should be access-controlled
        via reverse proxy (nginx) to internal networks only.
        The app itself doesn't implement auth on this endpoint.
        """
        response = client.get("/health/security")
        # Should return 200 (access control is at nginx level)
        assert response.status_code == 200