"""
Unit tests for DATABASE_URL parsing with URL decoding support.

Issue #1893: Production security hardening for Docker Compose.
"""

import os
import secrets
import pytest
from urllib.parse import unquote


def extract_db_password(database_url: str) -> str:
    """Extract database password from DATABASE_URL with URL decoding.

    Handles URL-encoded characters like %40 (@), %23 (#), %25 (%).

    Args:
        database_url: Database connection URL.

    Returns:
        Decoded password, or empty string if parsing fails.
    """
    if not database_url:
        return ""

    try:
        # Format: postgresql://user:password@host:port/dbname
        # Split after :// to get user:password@host:port/dbname
        auth_part = database_url.split("://", 1)[1].split("@", 1)[0]
        if ":" in auth_part:
            password = auth_part.split(":", 1)[1]
            return unquote(password)
        return ""
    except (IndexError, ValueError):
        return ""


class TestExtractDbPassword:
    """Tests for DATABASE_URL password extraction."""

    def test_standard_url(self):
        """Standard URL without encoding."""
        url = "postgresql://ace:mypassword@postgres:5432/ace"
        assert extract_db_password(url) == "mypassword"

    def test_url_encoded_at_sign(self):
        """Password containing @ encoded as %40."""
        url = "postgresql://ace:pass%40word@postgres:5432/ace"
        assert extract_db_password(url) == "pass@word"

    def test_url_encoded_hash(self):
        """Password containing # encoded as %23."""
        url = "postgresql://ace:p%23ssword@postgres:5432/ace"
        assert extract_db_password(url) == "p#ssword"

    def test_url_encoded_percent(self):
        """Password containing % encoded as %25."""
        url = "postgresql://ace:pass%25word@postgres:5432/ace"
        assert extract_db_password(url) == "pass%word"

    def test_multiple_encoded_chars(self):
        """Password with multiple encoded characters."""
        url = "postgresql://ace:pass%40%23%25word@postgres:5432/ace"
        assert extract_db_password(url) == "pass@#%word"

    def test_default_password_ace_secret(self):
        """Default password that should be rejected in production."""
        url = "postgresql://ace:ace-secret@postgres:5432/ace"
        assert extract_db_password(url) == "ace-secret"

    def test_empty_url(self):
        """Empty URL returns empty password."""
        assert extract_db_password("") == ""

    def test_none_url(self):
        """None URL returns empty password."""
        assert extract_db_password(None) == ""

    def test_url_without_password(self):
        """URL without password part returns empty."""
        url = "postgresql://ace@postgres:5432/ace"
        assert extract_db_password(url) == ""

    def test_url_with_port_in_password(self):
        """Password containing port-like number."""
        url = "postgresql://ace:pass5432word@postgres:5432/ace"
        assert extract_db_password(url) == "pass5432word"

    def test_complex_password(self):
        """Complex password with special characters."""
        # Password: P@ssw0rd!#$%^&*()
        # Note: ! $ & ' ( ) * + , ; = are safe in URL path but need encoding in userinfo
        url = "postgresql://ace:P%40ssw0rd%21%23%24%25%5E%26%2A%28%29@postgres:5432/ace"
        expected = "P@ssw0rd!#$%^&*()"
        assert extract_db_password(url) == expected

    def test_malformed_url_returns_empty(self):
        """Malformed URL returns empty string without raising."""
        # No @ separator
        url = "postgresql://ace:password"
        assert extract_db_password(url) == ""

    def test_url_with_query_params(self):
        """URL with query parameters."""
        url = "postgresql://ace:password@postgres:5432/ace?sslmode=require"
        assert extract_db_password(url) == "password"

    def test_url_with_ipv6_host(self):
        """URL with IPv6 host address."""
        url = "postgresql://ace:password@[::1]:5432/ace"
        assert extract_db_password(url) == "password"


class TestDefaultPasswordDetection:
    """Tests for default password detection."""

    def test_default_password_detected(self):
        """Default password 'ace-secret' is correctly identified."""
        url = "postgresql://ace:ace-secret@postgres:5432/ace"
        password = extract_db_password(url)
        assert password == "ace-secret"
        # In production mode, this should trigger an error
        is_default = password == "ace-secret"
        assert is_default is True

    def test_non_default_password_not_flagged(self):
        """Non-default password is not flagged as default."""
        url = "postgresql://ace:my-strong-password@postgres:5432/ace"
        password = extract_db_password(url)
        is_default = password == "ace-secret"
        assert is_default is False


class TestSecurityEnvIntegration:
    """Tests for integration with app/utils/security_env.py."""

    def test_weak_secret_detection_consistent(self):
        """Weak secret detection is consistent between Docker and Python layers."""
        from app.utils.security_env import is_weak_secret_value

        # Empty value is weak
        assert is_weak_secret_value("") is True
        assert is_weak_secret_value(None) is True

        # Known placeholders are weak
        assert is_weak_secret_value("change-me-in-production") is True
        assert is_weak_secret_value("dev-secret-key") is True
        assert is_weak_secret_value("default-secret-key") is True

        # Replace-with-random prefix is weak
        assert is_weak_secret_value("replace-with-random-dedicated-encryption-key") is True

        # Strong values are not weak
        assert is_weak_secret_value("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is False
        # Test with a dynamically generated strong value
        strong_key = secrets.token_hex(32)
        assert is_weak_secret_value(strong_key) is False

    def test_production_mode_detection(self):
        """Production mode detection works correctly."""
        from app.utils.security_env import is_production_environment

        # Development mode by default
        original = os.environ.get("FLASK_ENV")
        try:
            os.environ.pop("FLASK_ENV", None)
            assert is_production_environment() is False

            os.environ["FLASK_ENV"] = "production"
            assert is_production_environment() is True

            os.environ["FLASK_ENV"] = "development"
            assert is_production_environment() is False
        finally:
            if original:
                os.environ["FLASK_ENV"] = original
            else:
                os.environ.pop("FLASK_ENV", None)