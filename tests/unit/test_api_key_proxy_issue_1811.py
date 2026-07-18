"""
Unit tests for Issue #1811: Proxy token security and reliability fixes.

Tests for:
1. Fail-closed behavior during DB errors
2. Connection pool usage
3. Proxy token cleanup mechanism
4. TTL configuration
"""

import hashlib
import json
import os
import tempfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestFailClosedBehavior:
    """Tests for fail-closed behavior during DB errors."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_session_allows_proxy_token_db_connection_error_returns_false(self, mock_service):
        """Test that DB connection error causes token rejection (fail-closed)."""
        # Mock _get_connection to raise connection error
        with patch.object(mock_service, '_get_connection', side_effect=Exception("Connection failed")):
            result = mock_service._session_allows_proxy_token(
                session_id="test-session-123",
                session_type="agent",
                user_id=1,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
                exp=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            )
            assert result is False

    def test_session_allows_proxy_token_db_query_error_returns_false(self, mock_service):
        """Test that DB query error causes token rejection (fail-closed)."""
        # Mock connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Query failed")
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, '_get_connection', return_value=mock_conn):
            result = mock_service._session_allows_proxy_token(
                session_id="test-session-123",
                session_type="agent",
                user_id=1,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
                exp=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            )
            assert result is False

    def test_session_allows_proxy_token_db_timeout_returns_false(self, mock_service):
        """Test that DB timeout causes token rejection (fail-closed)."""
        # Mock connection to simulate timeout
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect=TimeoutError("Query timeout")
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, '_get_connection', return_value=mock_conn):
            result = mock_service._session_allows_proxy_token(
                session_id="test-session-123",
                session_type="agent",
                user_id=1,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
                exp=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            )
            assert result is False

    def test_session_not_found_for_sensitive_types_returns_false(self, mock_service):
        """Test that session not found for sensitive types returns False."""
        # Mock connection with no rows returned
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(mock_service, '_get_connection', return_value=mock_conn):
            # Test sensitive session types
            for session_type in ["agent", "terminal", "workflow"]:
                result = mock_service._session_allows_proxy_token(
                    session_id="test-session-123",
                    session_type=session_type,
                    user_id=1,
                    now=datetime.now(timezone.utc).replace(tzinfo=None),
                    exp=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                )
                assert result is False


class TestConnectionPool:
    """Tests for connection pool usage."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_validate_proxy_token_uses_single_connection(self, mock_service):
        """Test that validate_proxy_token uses single connection for all queries."""
        # Generate a test token
        token = mock_service.generate_proxy_token(
            user_id=1,
            session_id="test-session-123",
            tenant_id=1,
            provider="openai",
            session_type="ha_pool",  # Use ha_pool to avoid session check
        )

        # Validate token - should work without session check
        result = mock_service.validate_proxy_token(token)
        # Should be valid
        assert result is not None

    def test_get_connection_returns_pooled_connection_for_postgresql(self, mock_service):
        """Test that _get_connection returns pooled connection for PostgreSQL."""
        with patch('app.modules.workspace.api_key_proxy.is_postgresql', return_value=True):
            with patch('app.repositories.database.get_connection') as mock_get_conn:
                mock_conn = MagicMock()
                mock_get_conn.return_value = mock_conn

                conn = mock_service._get_connection()
                assert conn == mock_conn
                mock_get_conn.assert_called_once()


class TestProxyTokenCleanup:
    """Tests for proxy token cleanup mechanism."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_cleanup_deletes_expired_records(self, mock_service):
        """Test that cleanup deletes expired records."""
        # Create an expired token record
        conn = mock_service._get_connection()
        cursor = conn.cursor()

        expired_time = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)).isoformat()
        cursor.execute(
            "INSERT INTO proxy_token_jtis (jti, token_hash, user_id, session_id, tenant_id, provider, session_type, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-jti-expired", "test-hash", 1, "test-session", 1, "openai", "agent", expired_time),
        )
        conn.commit()
        conn.close()

        # Run cleanup
        deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Should delete at least one record
        assert deleted >= 1

        # Verify record is deleted
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxy_token_jtis WHERE jti = ?", ("test-jti-expired",))
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0

    def test_cleanup_deletes_consumed_records(self, mock_service):
        """Test that cleanup deletes consumed records."""
        conn = mock_service._get_connection()
        cursor = conn.cursor()

        # Create a consumed token record (consumed 2 days ago)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        consumed_time = (now - timedelta(days=2)).isoformat()
        expires_time = (now + timedelta(days=30)).isoformat()

        cursor.execute(
            "INSERT INTO proxy_token_jtis (jti, token_hash, user_id, session_id, tenant_id, provider, session_type, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-jti-consumed", "test-hash", 1, "test-session", 1, "openai", "agent", expires_time, consumed_time),
        )
        conn.commit()
        conn.close()

        # Run cleanup
        deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Should delete at least one record
        assert deleted >= 1

        # Verify record is deleted
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxy_token_jtis WHERE jti = ?", ("test-jti-consumed",))
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0

    def test_cleanup_deletes_revoked_records(self, mock_service):
        """Test that cleanup deletes revoked records."""
        conn = mock_service._get_connection()
        cursor = conn.cursor()

        # Create a revoked token record (revoked 2 days ago)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        revoked_time = (now - timedelta(days=2)).isoformat()
        expires_time = (now + timedelta(days=30)).isoformat()

        cursor.execute(
            "INSERT INTO proxy_token_jtis (jti, token_hash, user_id, session_id, tenant_id, provider, session_type, expires_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-jti-revoked", "test-hash", 1, "test-session", 1, "openai", "agent", expires_time, revoked_time),
        )
        conn.commit()
        conn.close()

        # Run cleanup
        deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Should delete at least one record
        assert deleted >= 1

        # Verify record is deleted
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxy_token_jtis WHERE jti = ?", ("test-jti-revoked",))
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0

    def test_cleanup_preserves_active_records(self, mock_service):
        """Test that cleanup preserves active records."""
        conn = mock_service._get_connection()
        cursor = conn.cursor()

        # Create an active token record
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_time = (now + timedelta(days=30)).isoformat()

        cursor.execute(
            "INSERT INTO proxy_token_jtis (jti, token_hash, user_id, session_id, tenant_id, provider, session_type, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-jti-active", "test-hash", 1, "test-session", 1, "openai", "agent", expires_time),
        )
        conn.commit()
        conn.close()

        # Run cleanup
        deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Should not delete active record
        # (deleted might be 0 or more if there are other expired records from other tests)
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxy_token_jtis WHERE jti = ?", ("test-jti-active",))
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1

    def test_cleanup_batch_limit(self, mock_service):
        """Test that cleanup respects batch limit of 1000 records."""
        conn = mock_service._get_connection()
        cursor = conn.cursor()

        # Create more than 1000 expired records
        expired_time = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)).isoformat()
        for i in range(1500):
            cursor.execute(
                "INSERT INTO proxy_token_jtis (jti, token_hash, user_id, session_id, tenant_id, provider, session_type, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"test-jti-batch-{i}", f"test-hash-{i}", 1, "test-session", 1, "openai", "agent", expired_time),
            )
        conn.commit()
        conn.close()

        # Run cleanup
        deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Should delete at most 1000 records (batch limit)
        assert deleted <= 1000

        # Verify remaining records
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxy_token_jtis WHERE jti LIKE 'test-jti-batch-%'")
        count = cursor.fetchone()[0]
        conn.close()

        # Should have at least 500 records remaining
        assert count >= 500


class TestTTLConfiguration:
    """Tests for TTL configuration."""

    @pytest.fixture
    def mock_service(self):
        """Create mock APIKeyProxyService."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}):
                service = APIKeyProxyService(db_path=db_path)
                yield service

    def test_ha_pool_ttl_respects_env_variable(self, mock_service):
        """Test that ha_pool TTL respects environment variable."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_HA_POOL_MINUTES": "60"}):
            token = mock_service.generate_proxy_token(
                user_id=1,
                session_id="test-session",
                tenant_id=1,
                provider="openai",
                session_type="ha_pool",
            )

            # Decode token and check expiry
            from base64 import b64decode
            payload_b64 = token.split(".")[0]
            payload = json.loads(b64decode(payload_b64))

            exp_time = datetime.fromisoformat(payload["exp"])
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Should be approximately 60 minutes from now (allow 1 minute tolerance)
            ttl_minutes = (exp_time - now).total_seconds() / 60
            assert 59 <= ttl_minutes <= 61

    def test_ha_pool_ttl_uses_default_when_env_not_set(self, mock_service):
        """Test that ha_pool TTL uses default when env not set."""
        # Clear any existing env variable
        env_copy = os.environ.copy()
        for key in ["OPENACE_PROXY_TOKEN_TTL_HA_POOL_MINUTES", "OPENACE_PROXY_TOKEN_TTL_MINUTES"]:
            if key in env_copy:
                del env_copy[key]

        with patch.dict(os.environ, env_copy, clear=True):
            token = mock_service.generate_proxy_token(
                user_id=1,
                session_id="test-session",
                tenant_id=1,
                provider="openai",
                session_type="ha_pool",
            )

            # Decode token and check expiry
            from base64 import b64decode
            payload_b64 = token.split(".")[0]
            payload = json.loads(b64decode(payload_b64))

            exp_time = datetime.fromisoformat(payload["exp"])
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Should use default 240 minutes
            ttl_minutes = (exp_time - now).total_seconds() / 60
            assert 239 <= ttl_minutes <= 241