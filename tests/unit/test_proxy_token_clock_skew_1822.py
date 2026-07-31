"""
Unit tests for Issue #1822: Proxy token clock skew tolerance.

Tests for:
1. Clock skew tolerance allows slightly expired tokens
2. Clock skew tolerance is configurable per session_type
3. Tokens outside clock skew tolerance are rejected
"""

import hashlib
import os
import tempfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestClockSkewTolerance:
    """Tests for clock skew tolerance (Issue #1822)."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_token_within_clock_skew_passes(self, mock_service):
        """Test that token within clock skew tolerance passes validation."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Token expired 20 seconds ago (within default 30s skew)
        exp = now - timedelta(seconds=20)

        # Use ha_pool session type which doesn't query agent_sessions
        result = mock_service._session_allows_proxy_token(
            session_id=None,
            session_type="ha_pool",
            user_id=1,
            now=now,
            exp=exp,
        )

        assert result is True, "Token within clock skew should pass"

    def test_token_outside_clock_skew_rejected(self, mock_service):
        """Test that token outside clock skew tolerance is rejected."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Token expired 40 seconds ago (outside default 30s skew)
        exp = now - timedelta(seconds=40)

        # Use ha_pool session type which doesn't query agent_sessions
        result = mock_service._session_allows_proxy_token(
            session_id=None,
            session_type="ha_pool",
            user_id=1,
            now=now,
            exp=exp,
        )

        assert result is False, "Token outside clock skew should be rejected"

    def test_custom_clock_skew_from_env(self, mock_service):
        """Test that clock skew can be configured via environment variable."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_CLOCK_SKEW_DEFAULT_SECONDS": "60"}):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            # Token expired 50 seconds ago (within custom 60s skew)
            exp = now - timedelta(seconds=50)

            # Use ha_pool session type
            result = mock_service._session_allows_proxy_token(
                session_id=None,
                session_type="ha_pool",
                user_id=1,
                now=now,
                exp=exp,
            )

            assert result is True, "Token within custom clock skew should pass"

    def test_ha_pool_specific_clock_skew(self, mock_service):
        """Test that ha_pool can have its own clock skew configuration."""
        with patch.dict(
            os.environ,
            {
                "OPENACE_PROXY_TOKEN_CLOCK_SKEW_DEFAULT_SECONDS": "30",
                "OPENACE_PROXY_TOKEN_CLOCK_SKEW_HA_POOL_SECONDS": "60",
            },
        ):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            # Token expired 50 seconds ago
            exp = now - timedelta(seconds=50)

            # ha_pool should use 60s tolerance
            result_ha_pool = mock_service._session_allows_proxy_token(
                session_id="test-session",
                session_type="ha_pool",
                user_id=1,
                now=now,
                exp=exp,
            )

            # agent should use 30s tolerance (token rejected)
            result_agent = mock_service._session_allows_proxy_token(
                session_id="test-session",
                session_type="agent",
                user_id=1,
                now=now,
                exp=exp,
            )

            assert result_ha_pool is True, "ha_pool with custom skew should pass"
            assert result_agent is False, "agent with default skew should reject"

    def test_get_clock_skew_seconds_default(self, mock_service):
        """Test _get_clock_skew_seconds returns default value."""
        skew = mock_service._get_clock_skew_seconds("agent")
        assert skew == 30, "Default clock skew should be 30 seconds"

    def test_get_clock_skew_seconds_custom_env(self, mock_service):
        """Test _get_clock_skew_seconds reads from environment."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_CLOCK_SKEW_DEFAULT_SECONDS": "45"}):
            skew = mock_service._get_clock_skew_seconds("agent")
            assert skew == 45, "Clock skew should read from environment"

    def test_get_clock_skew_seconds_session_type_specific(self, mock_service):
        """Test _get_clock_skew_seconds for session-type specific config."""
        with patch.dict(
            os.environ,
            {
                "OPENACE_PROXY_TOKEN_CLOCK_SKEW_DEFAULT_SECONDS": "30",
                "OPENACE_PROXY_TOKEN_CLOCK_SKEW_WEBUI_SECONDS": "60",
            },
        ):
            skew_webui = mock_service._get_clock_skew_seconds("webui")
            skew_agent = mock_service._get_clock_skew_seconds("agent")
            assert skew_webui == 60, "webui should use session-specific skew"
            assert skew_agent == 30, "agent should use default skew"

    def test_token_exactly_at_skew_boundary(self, mock_service):
        """Test token exactly at clock skew boundary."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Token expired exactly 30 seconds ago (at boundary)
        exp = now - timedelta(seconds=30)

        # At boundary, token should still pass (<= boundary)
        # Use ha_pool session type
        result = mock_service._session_allows_proxy_token(
            session_id=None,
            session_type="ha_pool",
            user_id=1,
            now=now,
            exp=exp,
        )

        assert result is True, "Token at exact boundary should pass"

    def test_clock_skew_with_conn_method(self, mock_service):
        """Test clock skew tolerance in _session_allows_proxy_token_with_conn."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Token expired 25 seconds ago (within skew)
        exp = now - timedelta(seconds=25)

        mock_conn = MagicMock()
        # Use ha_pool session type (doesn't query agent_sessions)
        result = mock_service._session_allows_proxy_token_with_conn(
            conn=mock_conn,
            session_id=None,
            session_type="ha_pool",
            user_id=1,
            now=now,
            exp=exp,
        )

        assert result is True, "Token within skew should pass with_conn method"


class TestClockSkewEdgeCases:
    """Edge case tests for clock skew tolerance."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_zero_clock_skew(self, mock_service):
        """Test that zero clock skew works correctly."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_CLOCK_SKEW_DEFAULT_SECONDS": "0"}):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            # Token expired 1 second ago
            exp = now - timedelta(seconds=1)

            result = mock_service._session_allows_proxy_token(
                session_id="test-session",
                session_type="agent",
                user_id=1,
                now=now,
                exp=exp,
            )

            assert result is False, "Token should be rejected with zero skew"

    def test_non_numeric_clock_skew_env_uses_default(self, mock_service):
        """Test that non-numeric clock skew env uses default."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_CLOCK_SKEW_HA_POOL_SECONDS": "invalid"}):
            skew = mock_service._get_clock_skew_seconds("ha_pool")
            # Should fall back to default
            assert skew == 30, "Invalid env should fall back to default"
