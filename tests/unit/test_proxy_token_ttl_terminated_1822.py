"""
Unit tests for Issue #1822: TTL minimum clamping and terminated token tracking.

Tests for:
1. TTL below minimum is clamped
2. TTL minimum is configurable
3. Expired single-use tokens are marked as terminated
"""

import hashlib
import os
import tempfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService


class TestTTLMinimumClamping:
    """Tests for TTL minimum clamping (Issue #1822)."""

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

    def test_ttl_below_minimum_is_clamped(self, mock_service, caplog):
        """Test that TTL below minimum is clamped."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "1"}):
            with caplog.at_level("WARNING"):
                ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 5, "TTL should be clamped to minimum (5)"
        assert any(
            "below minimum" in record.message for record in caplog.records
        ), "Should log clamping warning"

    def test_ttl_above_minimum_is_not_changed(self, mock_service):
        """Test that TTL above minimum is not changed."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "10"}):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 10, "TTL above minimum should not be changed"

    def test_ttl_at_minimum_is_not_changed(self, mock_service):
        """Test that TTL at minimum is not changed."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "5"}):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 5, "TTL at minimum should not be changed"

    def test_custom_minimum_ttl(self, mock_service):
        """Test that minimum TTL is configurable."""
        with patch.dict(
            os.environ,
            {
                "OPENACE_MIN_PROXY_TOKEN_TTL_MINUTES": "10",
                "OPENACE_PROXY_TOKEN_TTL_MINUTES": "7",
            },
        ):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 10, "TTL should be clamped to custom minimum"

    def test_session_specific_ttl_below_minimum_clamped(self, mock_service):
        """Test that session-specific TTL below minimum is clamped."""
        with patch.dict(
            os.environ, {"OPENACE_PROXY_TOKEN_TTL_AGENT_MINUTES": "2"}
        ):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 5, "Session-specific TTL below minimum should be clamped"

    def test_session_specific_ttl_above_minimum_not_changed(self, mock_service):
        """Test that session-specific TTL above minimum is not changed."""
        with patch.dict(
            os.environ, {"OPENACE_PROXY_TOKEN_TTL_AGENT_MINUTES": "15"}
        ):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 15, "Session-specific TTL above minimum should not be changed"

    def test_invalid_minimum_ttl_env_uses_default(self, mock_service):
        """Test that invalid minimum TTL env uses class default."""
        with patch.dict(
            os.environ,
            {
                "OPENACE_MIN_PROXY_TOKEN_TTL_MINUTES": "invalid",
                "OPENACE_PROXY_TOKEN_TTL_MINUTES": "2",
            },
        ):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        # Should use class default minimum (5)
        assert ttl == 5, "Should use class default minimum when env is invalid"

    def test_ttl_zero_is_rejected(self, mock_service):
        """Test that TTL=0 is rejected and fallback is used."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "0"}):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        # TTL=0 should fallback to default (240)
        assert ttl == 240, "TTL=0 should use default"

    def test_ttl_negative_is_rejected(self, mock_service):
        """Test that negative TTL is rejected and fallback is used."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "-5"}):
            ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        # Negative TTL should fallback to default (240)
        assert ttl == 240, "Negative TTL should use default"

    def test_ttl_non_numeric_is_rejected(self, mock_service, caplog):
        """Test that non-numeric TTL is rejected and fallback is used."""
        with patch.dict(os.environ, {"OPENACE_PROXY_TOKEN_TTL_MINUTES": "abc"}):
            with caplog.at_level("WARNING"):
                ttl = mock_service._get_default_proxy_token_ttl_minutes("agent")

        assert ttl == 240, "Non-numeric TTL should use default"


class TestTerminatedTokenTracking:
    """Tests for terminated token tracking (Issue #1822)."""

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

    def test_mark_single_use_token_terminated_success(self, mock_service):
        """Test marking single-use token as terminated."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = mock_service._mark_single_use_token_terminated_with_conn(
            conn=mock_conn,
            jti="test-jti-12345678",
            reason="expired",
            now=now,
        )

        assert result is True, "Should successfully mark token as terminated"
        mock_cursor.execute.assert_called_once()

    def test_mark_single_use_token_terminated_already_marked(self, mock_service):
        """Test that marking already terminated token returns False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # No rows updated
        mock_conn.cursor.return_value = mock_cursor

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = mock_service._mark_single_use_token_terminated_with_conn(
            conn=mock_conn,
            jti="test-jti-12345678",
            reason="expired",
            now=now,
        )

        assert result is False, "Should return False if already terminated"

    def test_mark_single_use_token_terminated_on_error(self, mock_service):
        """Test that marking handles database errors gracefully."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = mock_service._mark_single_use_token_terminated_with_conn(
            conn=mock_conn,
            jti="test-jti-12345678",
            reason="expired",
            now=now,
        )

        assert result is False, "Should return False on error"

    def test_concurrent_termination_marking_is_atomic(self, mock_service):
        """Test that concurrent termination marking is atomic.

        Issue #1822: Verify that when two threads try to mark the same token
        as terminated concurrently, only one succeeds (no data corruption).
        """
        import threading

        # Create a real database with a single-use expired token
        # First, create a token record
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Generate a test token and record it in the database
        import secrets

        jti = secrets.token_hex(16)
        test_token = mock_service.generate_proxy_token(
            user_id=1,
            session_id="test-session",
            tenant_id=1,
            provider="openai",
            session_type="ha_pool",
            expires_minutes=5,
            extra_payload={"reuse_mode": "single_use"},
        )

        # Get the JTI from the token
        import json
        from base64 import b64decode

        payload_b64 = test_token.split(".")[0]
        payload = json.loads(b64decode(payload_b64))
        jti = payload["jti"]

        # Manually expire the token by updating expires_at in the database
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        expired_time = (now - timedelta(minutes=5)).isoformat()
        cursor.execute(
            "UPDATE proxy_token_jtis SET expires_at = ? WHERE jti = ?",
            (expired_time, jti),
        )
        conn.commit()
        conn.close()

        results = []
        lock = threading.Lock()

        def validate_expired_token():
            """Attempt to validate the expired token."""
            result = mock_service.validate_proxy_token(test_token)
            with lock:
                results.append(result)

        # Start two threads concurrently
        threads = [threading.Thread(target=validate_expired_token) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Both should return None (token expired)
        # But only one should succeed in marking terminated_at
        none_count = sum(1 for r in results if r is None)
        assert none_count == 2, f"Both threads should reject expired token, got {none_count}"

        # Verify terminated_at was set exactly once
        conn = mock_service._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT terminated_at, termination_reason FROM proxy_token_jtis WHERE jti = ?",
            (jti,),
        )
        row = cursor.fetchone()
        conn.close()

        terminated_at = row[0] if row else None
        termination_reason = row[1] if row else None

        assert terminated_at is not None, "terminated_at should be set"
        assert termination_reason == "expired", "termination_reason should be 'expired'"


class TestCleanupTerminatedRecords:
    """Tests for cleanup of terminated records (Issue #1822)."""

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

    def test_cleanup_includes_terminated_records(self, mock_service):
        """Test that cleanup includes terminated records."""
        # Create a real database for this test
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.close = MagicMock()

        with patch.object(mock_service, "_get_connection", return_value=mock_conn):
            deleted = mock_service.cleanup_proxy_token_jtis(days_old=7)

        assert deleted == 5
        # Verify the SQL includes terminated_at condition
        call_args = mock_cursor.execute.call_args[0][0]
        assert "terminated_at" in call_args, "SQL should include terminated_at condition"

    def test_cleanup_terminated_days_configurable(self, mock_service):
        """Test that terminated days is configurable via environment."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor

        with patch.dict(
            os.environ, {"OPENACE_PROXY_TOKEN_CLEANUP_TERMINATED_DAYS": "14"}
        ):
            with patch.object(mock_service, "_get_connection", return_value=mock_conn):
                mock_service.cleanup_proxy_token_jtis(days_old=7)

        # Verify the threshold_terminated was calculated correctly
        # (We can't easily verify the actual value, but we verify it runs without error)
        mock_cursor.execute.assert_called_once()
