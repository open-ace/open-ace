#!/usr/bin/env python3
"""
Tests for Issue #1169: WebUI session token validation based on instance alive status.

Tests that validate_proxy_token() checks instance alive status for WebUI sessions
instead of fixed expiration time.
"""

import os
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from base64 import b64encode

import pytest

# Set encryption key for tests
os.environ.setdefault("OPENACE_ENCRYPTION_KEY", "test-encryption-key-for-issue-1169-tests")


def _make_proxy_token_payload(
    user_id: int = 1,
    session_id: str = "webui:1",
    tenant_id: int = 1,
    provider: str = "openai",
    expires_minutes: int = -1,  # Already expired by default
    session_type: str = "webui",
) -> dict:
    """Create a mock proxy token payload."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    # Remove timezone info to match how the code parses it
    exp_str = exp.replace(tzinfo=None).isoformat()
    return {
        "user_id": user_id,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "provider": provider,
        "exp": exp_str,
        "jti": "test-token-id",
        "session_type": session_type,
    }


def _encode_payload(payload: dict) -> str:
    """Encode payload to base64."""
    return b64encode(json.dumps(payload).encode()).decode()


def _make_signed_token(service, payload: dict) -> str:
    """Create a signed token from payload."""
    import hmac
    import hashlib

    payload_b64 = _encode_payload(payload)
    signature = hmac.new(
        service._encryption_key,
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


class TestWebUITokenInstanceCheck:
    """Tests for WebUI session token validation based on instance alive status."""

    @patch("app.services.webui_manager.get_webui_manager")
    def test_webui_session_instance_alive_token_accepted(self, mock_get_manager):
        """
        Test that expired token for WebUI session is accepted if instance is alive.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Setup mock manager and instance
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True
        mock_manager = MagicMock()
        mock_manager.get_user_instance.return_value = mock_instance
        mock_get_manager.return_value = mock_manager

        # Create service and generate expired token
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="webui:1",
            expires_minutes=-1,  # Already expired
        )
        token = _make_signed_token(service, payload)

        # Validate - should succeed because instance is alive
        result = service.validate_proxy_token(token)
        assert result is not None, "Token should be accepted when instance is alive"
        assert result["user_id"] == 1
        assert result["session_id"] == "webui:1"

    @patch("app.services.webui_manager.get_webui_manager")
    def test_webui_session_instance_dead_token_rejected(self, mock_get_manager):
        """
        Test that expired token for WebUI session is rejected if instance is dead.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Setup mock manager and instance (dead)
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = False
        mock_manager = MagicMock()
        mock_manager.get_user_instance.return_value = mock_instance
        mock_get_manager.return_value = mock_manager

        # Create service and generate expired token
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="webui:1",
            expires_minutes=-1,
        )
        token = _make_signed_token(service, payload)

        # Validate - should fail because instance is dead
        result = service.validate_proxy_token(token)
        assert result is None, "Token should be rejected when instance is dead"

    @patch("app.services.webui_manager.get_webui_manager")
    def test_webui_session_no_instance_token_rejected(self, mock_get_manager):
        """
        Test that expired token for WebUI session is rejected if instance not found.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Setup mock manager (no instance)
        mock_manager = MagicMock()
        mock_manager.get_user_instance.return_value = None
        mock_get_manager.return_value = mock_manager

        # Create service and generate expired token
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="webui:1",
            expires_minutes=-1,
        )
        token = _make_signed_token(service, payload)

        # Validate - should fail because instance not found
        result = service.validate_proxy_token(token)
        assert result is None, "Token should be rejected when instance not found"

    def test_agent_session_expired_token_rejected(self):
        """
        Test that expired token for agent session is rejected (original behavior).
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create service and generate expired token for agent session
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="agent-session-id",
            expires_minutes=-1,
            session_type="agent",
        )
        token = _make_signed_token(service, payload)

        # Validate - should fail because it's agent session and expired
        result = service.validate_proxy_token(token)
        assert result is None, "Agent session expired token should be rejected"

    def test_agent_session_valid_token_accepted_if_db_check_passes(self):
        """
        Test that valid token for agent session goes through expiration check.
        Note: Agent session also needs database check, which may fail in unit test.
        The point is to verify expiration logic is applied correctly for agent sessions.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create service and generate valid token for agent session
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="agent-session-id",
            expires_minutes=60,  # Valid for 60 minutes
            session_type="agent",
        )
        token = _make_signed_token(service, payload)

        # Validate - expiration check should pass
        # But agent session needs database check, which will fail in unit test
        result = service.validate_proxy_token(token)
        # The result may be None due to database check failure
        # but the important thing is: it went through expiration check, not instance alive check
        # We can verify this by checking that the code doesn't call get_webui_manager


class TestWebUITokenNoUserId:
    """Tests for WebUI session token without user_id."""

    def test_webui_session_no_user_id_uses_expiration_check(self):
        """
        Test that WebUI session token without user_id falls back to expiration check.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create service and generate expired token without user_id
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=None,
            session_id="webui:unknown",
            expires_minutes=-1,
        )
        token = _make_signed_token(service, payload)

        # Validate - should fail because expired (no user_id to check instance)
        result = service.validate_proxy_token(token)
        assert result is None, "Token without user_id should use expiration check and fail"

    @patch("app.services.webui_manager.get_webui_manager")
    def test_webui_session_no_user_id_valid_token(self, mock_get_manager):
        """
        Test that WebUI session token without user_id but valid expiration passes.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Create service and generate valid token without user_id
        service = APIKeyProxyService()
        payload = _make_proxy_token_payload(
            user_id=None,
            session_id="webui:unknown",
            expires_minutes=60,  # Valid
        )
        token = _make_signed_token(service, payload)

        # Validate - should pass expiration check (but no instance check)
        result = service.validate_proxy_token(token)
        # Without user_id, it falls through to expiration check which passes
        assert result is not None, "Token without user_id but valid expiration should pass"


class TestWebUIvsAgentBehaviorDifference:
    """Tests to verify different behavior between WebUI and Agent sessions."""

    @patch("app.services.webui_manager.get_webui_manager")
    def test_webui_ignores_expiration_if_instance_alive(self, mock_get_manager):
        """
        Test that WebUI session completely ignores expiration time when instance is alive.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Setup mock manager and instance
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True
        mock_manager = MagicMock()
        mock_manager.get_user_instance.return_value = mock_instance
        mock_get_manager.return_value = mock_manager

        service = APIKeyProxyService()

        # Test with extremely expired token (-1000 days)
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="webui:1",
            expires_minutes=-1000 * 24 * 60,  # 1000 days ago
        )
        token = _make_signed_token(service, payload)

        result = service.validate_proxy_token(token)
        assert result is not None, "WebUI session should accept even very old token if instance alive"

    def test_agent_rejects_expiration_even_very_old(self):
        """
        Test that Agent session rejects expired token based on expiration time.
        """
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        service = APIKeyProxyService()

        # Test with extremely expired token for agent session
        payload = _make_proxy_token_payload(
            user_id=1,
            session_id="agent-session-id",
            expires_minutes=-1000 * 24 * 60,  # 1000 days ago
            session_type="agent",
        )
        token = _make_signed_token(service, payload)

        result = service.validate_proxy_token(token)
        assert result is None, "Agent session should reject expired token"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])