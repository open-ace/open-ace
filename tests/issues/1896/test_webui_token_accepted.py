#!/usr/bin/env python3
"""
Test that WebUI tokens from query parameters are accepted.

Issue #1896: WebUI tokens (short-lived, scoped) should still be accepted
from query parameters for iframe integration scenarios.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _make_app():
    """Create a minimal Flask app with test routes."""
    from flask import Flask, g, jsonify

    from app.auth.decorators import admin_required

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    @app.route("/api/admin")
    @admin_required
    def admin_route():
        return jsonify({"user_id": g.user_id, "role": g.user_role})

    return app


MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "must_change_password": False,
}

MOCK_ADMIN_USER = {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "tenant_id": 1,
    "must_change_password": False,
}


class TestWebuiTokenAccepted:
    """Test that WebUI tokens from query parameters are accepted."""

    def test_webui_token_format_valid(self):
        """Test that valid WebUI token format is recognized."""
        from app.auth.decorators import _looks_like_webui_token

        # Valid WebUI token format: user_id:port:random:signature
        valid_token = "1:3100:1234567890abcdef1234567890abcdef:1234567890abcdef"
        assert _looks_like_webui_token(valid_token) is True

    def test_webui_token_format_invalid_too_few_parts(self):
        """Test that invalid WebUI token (too few parts) is rejected."""
        from app.auth.decorators import _looks_like_webui_token

        invalid_token = "1:3100:1234567890abcdef"
        assert _looks_like_webui_token(invalid_token) is False

    def test_webui_token_format_invalid_port_range(self):
        """Test that invalid WebUI token (port out of range) is rejected."""
        from app.auth.decorators import _looks_like_webui_token

        # Port 80 is below valid range (1024-65535)
        invalid_token = "1:80:1234567890abcdef1234567890abcdef:1234567890abcdef"
        assert _looks_like_webui_token(invalid_token) is False

    def test_webui_token_format_invalid_random_length(self):
        """Test that invalid WebUI token (wrong random length) is rejected."""
        from app.auth.decorators import _looks_like_webui_token

        # Random part is not 32 characters
        invalid_token = "1:3100:1234567890abcdef:1234567890abcdef"
        assert _looks_like_webui_token(invalid_token) is False

    def test_webui_token_format_invalid_signature_length(self):
        """Test that invalid WebUI token (wrong signature length) is rejected."""
        from app.auth.decorators import _looks_like_webui_token

        # Signature part is not 16 characters
        invalid_token = "1:3100:1234567890abcdef1234567890abcdef:1234567890abcdef1234567890abcdef"
        assert _looks_like_webui_token(invalid_token) is False

    def test_webui_token_format_invalid_non_hex(self):
        """Test that invalid WebUI token (non-hex characters) is rejected."""
        from app.auth.decorators import _looks_like_webui_token

        # Random part contains non-hex characters
        invalid_token = "1:3100:ghij1234567890abcdef12345678:1234567890abcdef"
        assert _looks_like_webui_token(invalid_token) is False

    def test_webui_token_accepted_from_query(self):
        """WebUI token from query param should be accepted (with valid format)."""
        app = _make_app()

        valid_webui_token = "1:3100:1234567890abcdef1234567890abcdef:1234567890abcdef"

        # Mock WebUIManager.validate_token to return success
        mock_manager = MagicMock()
        mock_manager.validate_token.return_value = (True, 1, None)

        # Mock user repo to return admin user
        mock_user_repo = MagicMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_ADMIN_USER

        with patch(
            "app.services.webui_manager.get_webui_manager",
            return_value=mock_manager,
        ):
            with patch(
                "app.repositories.user_repo.UserRepository",
                return_value=mock_user_repo,
            ):
                with app.test_client() as client:
                    resp = client.get(f"/api/admin?token={valid_webui_token}")
                    # Should succeed with WebUI token
                    assert resp.status_code == 200
                    data = resp.get_json()
                    assert data["user_id"] == 1

    def test_non_webui_token_rejected_from_query(self):
        """Non-WebUI-format token from query param should be rejected."""
        app = _make_app()

        # Token that doesn't match WebUI format (regular session token)
        non_webui_token = "abc123def456ghi789"

        with app.test_client() as client:
            resp = client.get(f"/api/admin?token={non_webui_token}")
            # Should reject because it's not a WebUI token format
            assert resp.status_code == 401

    def test_empty_token_rejected(self):
        """Empty token should be rejected."""
        from app.auth.decorators import _looks_like_webui_token

        assert _looks_like_webui_token("") is False
        assert _looks_like_webui_token(None) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])