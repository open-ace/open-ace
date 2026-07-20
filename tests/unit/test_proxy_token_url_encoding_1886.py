"""
Unit tests for Issue #1886: URL-encoded proxy token handling.

Tests that proxy tokens with URL-encoded base64 characters are correctly
decoded and validated.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestURLEncodedProxyToken:
    """Tests for URL-encoded proxy token validation."""

    @pytest.fixture
    def service(self):
        """Create APIKeyProxyService instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_url_encoded_plus_in_base64(self, service):
        """Test that + character (%2B) in base64 is correctly decoded."""
        # Generate a token that will contain + in base64
        token = service.generate_proxy_token(
            user_id=1,
            session_id="test-session-abc+def",
            tenant_id=1,
            provider="anthropic",
            session_type="ha_pool",  # Use ha_pool to avoid session check
        )

        # URL-encode the token (+ becomes %2B)
        url_encoded_token = token.replace("+", "%2B")

        # Validate the URL-encoded token
        result = service.validate_proxy_token(url_encoded_token)

        # Should successfully validate
        assert result is not None
        assert result["user_id"] == 1
        assert result["session_id"] == "test-session-abc+def"

    def test_url_encoded_slash_in_base64(self, service):
        """Test that / character (%2F) in base64 is correctly decoded."""
        token = service.generate_proxy_token(
            user_id=2,
            session_id="test-session/with/slash",
            tenant_id=1,
            provider="openai",
            session_type="ha_pool",
        )

        # URL-encode the token (/ becomes %2F)
        url_encoded_token = token.replace("/", "%2F")

        # Validate the URL-encoded token
        result = service.validate_proxy_token(url_encoded_token)

        # Should successfully validate
        assert result is not None
        assert result["user_id"] == 2
        assert result["session_id"] == "test-session/with/slash"

    def test_non_encoded_token_still_works(self, service):
        """Test that non-encoded tokens still work (backward compatibility)."""
        token = service.generate_proxy_token(
            user_id=3,
            session_id="test-session",
            tenant_id=1,
            provider="anthropic",
            session_type="ha_pool",
        )

        # Validate without any encoding
        result = service.validate_proxy_token(token)

        # Should successfully validate
        assert result is not None
        assert result["user_id"] == 3

    def test_empty_token_returns_none(self, service):
        """Test that empty token returns None."""
        result = service.validate_proxy_token("")
        assert result is None

        result = service.validate_proxy_token(None)
        assert result is None
