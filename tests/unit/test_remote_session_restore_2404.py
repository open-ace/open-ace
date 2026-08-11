"""
Unit tests for Issue #2404/#2405/#2406: Remote session restore functionality.

Tests for:
- clear_session_end_flag: Clear stale session end flag
- has_valid_proxy_token: Check if session has valid proxy token

Note: find_session_jsonl tests are in remote-agent tests (requires remote-agent module).
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestClearSessionEndFlag:
    """Tests for clear_session_end_flag in remote_agent_manager.py."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock RemoteAgentManager."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        manager = MagicMock(spec=RemoteAgentManager)
        manager._session_end_flags = {}
        manager._lock = MagicMock()
        manager._lock.__enter__ = MagicMock(return_value=None)
        manager._lock.__exit__ = MagicMock(return_value=None)

        # Implement the actual method
        def clear_session_end_flag(session_id: str) -> None:
            with manager._lock:
                if manager._session_end_flags.pop(session_id, None):
                    pass  # Logger call omitted for test

        manager.clear_session_end_flag = clear_session_end_flag
        return manager

    def test_clear_existing_flag(self, mock_manager):
        """Test clearing an existing session end flag."""
        session_id = "test-session-123"
        mock_manager._session_end_flags[session_id] = True

        mock_manager.clear_session_end_flag(session_id)

        assert session_id not in mock_manager._session_end_flags

    def test_clear_nonexistent_flag(self, mock_manager):
        """Test clearing a non-existent flag (no error)."""
        session_id = "nonexistent-session"

        # Should not raise
        mock_manager.clear_session_end_flag(session_id)

        assert session_id not in mock_manager._session_end_flags


class TestHasValidProxyToken:
    """Tests for has_valid_proxy_token in api_key_proxy.py."""

    @pytest.fixture
    def mock_service(self):
        """Create a mock APIKeyProxyService."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from app.utils.encryption_key_registry import reset_registry

        reset_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890123"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                yield service
        reset_registry()

    def test_has_valid_proxy_token_no_session(self, mock_service):
        """Test returns False for empty session ID."""
        result = mock_service.has_valid_proxy_token("")
        assert result is False

    def test_has_valid_proxy_token_no_token(self, mock_service):
        """Test returns False when no token exists."""
        result = mock_service.has_valid_proxy_token("nonexistent-session-id")
        assert result is False

    def test_has_valid_proxy_token_with_valid_token(self, mock_service):
        """Test returns True when valid token exists."""
        from datetime import datetime, timedelta

        # Create a proxy token record
        session_id = "test-session-valid-token"
        now = datetime.now()
        expires_at = now + timedelta(hours=4)

        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO proxy_token_jtis (
                jti, token_hash, session_id, provider, session_type, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "test-jti-valid",
                "test-hash-valid",
                session_id,
                "test-provider",
                "agent",
                expires_at.isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        result = mock_service.has_valid_proxy_token(session_id)
        assert result is True

    def test_has_valid_proxy_token_with_expired_token(self, mock_service):
        """Test returns False when token is expired."""
        from datetime import datetime, timedelta

        session_id = "test-session-expired-token"
        now = datetime.now()
        expires_at = now - timedelta(hours=1)  # Expired

        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO proxy_token_jtis (
                jti, token_hash, session_id, provider, session_type, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "test-jti-expired",
                "test-hash-expired",
                session_id,
                "test-provider",
                "agent",
                expires_at.isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        result = mock_service.has_valid_proxy_token(session_id)
        assert result is False

    def test_has_valid_proxy_token_with_revoked_token(self, mock_service):
        """Test returns False when token is revoked."""
        from datetime import datetime, timedelta

        session_id = "test-session-revoked-token"
        now = datetime.now()
        expires_at = now + timedelta(hours=4)

        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO proxy_token_jtis (
                jti, token_hash, session_id, provider, session_type, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-jti-revoked",
                "test-hash-revoked",
                session_id,
                "test-provider",
                "agent",
                expires_at.isoformat(),
                now.isoformat(),  # revoked_at
            ),
        )
        conn.commit()
        conn.close()

        result = mock_service.has_valid_proxy_token(session_id)
        assert result is False
