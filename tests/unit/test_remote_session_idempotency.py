"""Unit tests for Issue #3206: Remote session idempotency and dedup.

Tests idempotency key handling, dedup window, and concurrent session limits
for POST /api/remote/sessions.
"""

import hashlib
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# Import constants from remote.py
from app.routes.remote import (
    _DEDUP_KEY_PREFIX,
    _IDEMPOTENCY_KEY_PREFIX,
    IDEMPOTENCY_KEY_TTL_SECONDS,
    REMOTE_SESSION_DEDUP_WINDOW_SECONDS,
    RETRY_AFTER_SECONDS,
    _check_dedup_window,
    _check_idempotency_key,
    _check_remote_session_concurrent_limit,
    _get_dedup_cache_key,
    _get_idempotency_cache_key,
    _store_dedup_window,
    _store_idempotency_key,
)


class TestIdempotencyKeyHelpers:
    """Tests for idempotency key helper functions."""

    def test_get_idempotency_cache_key(self):
        """Test idempotency cache key generation."""
        key = "test-key-123"
        result = _get_idempotency_cache_key(key)
        assert result == f"{_IDEMPOTENCY_KEY_PREFIX}{key}"

    def test_get_dedup_cache_key(self):
        """Test dedup cache key generation."""
        user_id = 1
        machine_id = "machine-uuid-123"
        project_path = "/home/user/project"

        result = _get_dedup_cache_key(user_id, machine_id, project_path)

        # Verify format: prefix + user_id + machine_id prefix + path hash
        path_hash = hashlib.sha256(project_path.encode(), usedforsecurity=False).hexdigest()[:16]
        expected = f"{_DEDUP_KEY_PREFIX}{user_id}:{machine_id[:8]}:{path_hash}"
        assert result == expected

    def test_get_dedup_cache_key_special_chars(self):
        """Test dedup cache key handles special characters in path."""
        user_id = 1
        machine_id = "machine-uuid-123"
        project_path = "/home/user/path with spaces & special!@#$%"

        result = _get_dedup_cache_key(user_id, machine_id, project_path)

        # Should not raise and should produce a valid key
        assert result.startswith(_DEDUP_KEY_PREFIX)
        assert " " not in result  # No spaces in cache key
        assert "&" not in result

    def test_check_idempotency_key_exists(self):
        """Test checking idempotency key that exists in cache."""
        idempotency_key = str(uuid.uuid4())
        cached_data = {"session_id": "test-session-id"}

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.get.return_value = cached_data
            mock_get_cache.return_value = mock_cache

            result = _check_idempotency_key(idempotency_key)

            assert result == cached_data
            mock_cache.get.assert_called_once_with(_get_idempotency_cache_key(idempotency_key))

    def test_check_idempotency_key_not_exists(self):
        """Test checking idempotency key that does not exist."""
        idempotency_key = str(uuid.uuid4())

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_get_cache.return_value = mock_cache

            result = _check_idempotency_key(idempotency_key)

            assert result is None

    def test_check_idempotency_key_empty(self):
        """Test checking empty idempotency key."""
        result = _check_idempotency_key("")
        assert result is None

    def test_check_idempotency_key_cache_error(self):
        """Test handling cache errors gracefully."""
        idempotency_key = str(uuid.uuid4())

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_get_cache.side_effect = Exception("Cache error")

            result = _check_idempotency_key(idempotency_key)

            # Should return None and not raise
            assert result is None

    def test_store_idempotency_key(self):
        """Test storing idempotency key in cache."""
        idempotency_key = str(uuid.uuid4())
        session_data = {"session_id": "test-session-id"}

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.set.return_value = True
            mock_get_cache.return_value = mock_cache

            result = _store_idempotency_key(idempotency_key, session_data)

            assert result is True
            mock_cache.set.assert_called_once_with(
                _get_idempotency_cache_key(idempotency_key),
                session_data,
                IDEMPOTENCY_KEY_TTL_SECONDS,
            )

    def test_store_idempotency_key_empty(self):
        """Test storing empty idempotency key does nothing."""
        result = _store_idempotency_key("", {"session_id": "test"})
        assert result is False


class TestDedupWindowHelpers:
    """Tests for dedup window helper functions."""

    def test_check_dedup_window_hit(self):
        """Test dedup window returns cached session."""
        user_id = 1
        machine_id = "machine-uuid"
        project_path = "/home/user/project"
        cached_data = {"session_id": "cached-session-id"}

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.get.return_value = cached_data
            mock_get_cache.return_value = mock_cache

            result = _check_dedup_window(user_id, machine_id, project_path)

            assert result == cached_data

    def test_check_dedup_window_miss(self):
        """Test dedup window returns None when no match."""
        user_id = 1
        machine_id = "machine-uuid"
        project_path = "/home/user/project"

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_get_cache.return_value = mock_cache

            result = _check_dedup_window(user_id, machine_id, project_path)

            assert result is None

    def test_store_dedup_window(self):
        """Test storing session in dedup window."""
        user_id = 1
        machine_id = "machine-uuid"
        project_path = "/home/user/project"
        session_data = {"session_id": "test-session-id"}

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.set.return_value = True
            mock_get_cache.return_value = mock_cache

            result = _store_dedup_window(user_id, machine_id, project_path, session_data)

            assert result is True
            mock_cache.set.assert_called_once()

    def test_dedup_window_cache_error(self):
        """Test handling cache errors gracefully in dedup window."""
        user_id = 1
        machine_id = "machine-uuid"
        project_path = "/home/user/project"

        with patch("app.utils.cache.get_cache") as mock_get_cache:
            mock_get_cache.side_effect = Exception("Cache error")

            result = _check_dedup_window(user_id, machine_id, project_path)

            # Should return None and not raise
            assert result is None


class TestConcurrentSessionLimit:
    """Tests for concurrent session limit check."""

    @pytest.fixture
    def app(self):
        """Create a Flask app for testing."""
        app = Flask(__name__)
        return app

    def test_concurrent_limit_below_limit(self, app):
        """Test user below concurrent session limit."""
        user_id = 1

        with (
            app.app_context(),
            patch("app.repositories.user_repo.UserRepository") as mock_user_repo,
            patch("app.repositories.tenant_repo.TenantRepository") as mock_tenant_repo,
            patch("app.repositories.database.get_db_connection") as mock_db,
        ):
            # Mock user lookup
            mock_user = {"tenant_id": 1}
            mock_user_repo.return_value.get_user_by_id.return_value = mock_user

            # Mock tenant lookup with max_sessions_per_user = 5
            mock_tenant = MagicMock()
            mock_tenant.quota.max_sessions_per_user = 5
            mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

            # Mock DB query - 3 active sessions (below limit)
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {"cnt": 3}
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=None)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.return_value = mock_conn

            result = _check_remote_session_concurrent_limit(user_id)

            # Should return None (no error)
            assert result is None

    def test_concurrent_limit_at_limit(self, app):
        """Test user at concurrent session limit returns 429."""
        user_id = 1

        with (
            app.app_context(),
            patch("app.repositories.user_repo.UserRepository") as mock_user_repo,
            patch("app.repositories.tenant_repo.TenantRepository") as mock_tenant_repo,
            patch("app.repositories.database.get_db_connection") as mock_db,
        ):
            # Mock user lookup
            mock_user = {"tenant_id": 1}
            mock_user_repo.return_value.get_user_by_id.return_value = mock_user

            # Mock tenant lookup with max_sessions_per_user = 5
            mock_tenant = MagicMock()
            mock_tenant.quota.max_sessions_per_user = 5
            mock_tenant_repo.return_value.get_by_id.return_value = mock_tenant

            # Mock DB query - 5 active sessions (at limit)
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {"cnt": 5}
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=None)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.return_value = mock_conn

            result = _check_remote_session_concurrent_limit(user_id)

            # Should return 429 response
            assert result is not None
            response, status = result
            assert status == 429
            # Check JSON response contains error and retry_after
            json_data = response.get_json()
            assert "error" in json_data
            assert "retry_after" in json_data
            assert json_data["retry_after"] == RETRY_AFTER_SECONDS
            # Check Retry-After header
            assert "Retry-After" in response.headers

    def test_concurrent_limit_user_not_found(self, app):
        """Test user not found returns 404."""
        user_id = 999

        with (
            app.app_context(),
            patch("app.repositories.user_repo.UserRepository") as mock_user_repo,
        ):
            mock_user_repo.return_value.get_user_by_id.return_value = None

            result = _check_remote_session_concurrent_limit(user_id)

            # Should return 404 response
            assert result is not None
            _response, status = result
            assert status == 404

    def test_concurrent_limit_fail_open(self, app):
        """Test that errors fail open (allow creation)."""
        user_id = 1

        with (
            app.app_context(),
            patch("app.repositories.user_repo.UserRepository") as mock_user_repo,
        ):
            mock_user_repo.side_effect = Exception("DB error")

            result = _check_remote_session_concurrent_limit(user_id)

            # Should return None (fail open)
            assert result is None


class TestConstants:
    """Tests for environment variable constants."""

    def test_default_constants(self):
        """Test default values for constants."""
        # These are the defaults from the code
        assert IDEMPOTENCY_KEY_TTL_SECONDS == 60
        assert REMOTE_SESSION_DEDUP_WINDOW_SECONDS == 5
        assert RETRY_AFTER_SECONDS == 60

    def test_custom_idempotency_ttl(self):
        """Test custom idempotency TTL via environment variable."""
        with patch.dict(os.environ, {"IDEMPOTENCY_KEY_TTL_SECONDS": "120"}):
            # Re-import to get new value
            from importlib import reload

            import app.routes.remote as remote_module

            reload(remote_module)
            assert remote_module.IDEMPOTENCY_KEY_TTL_SECONDS == 120

    def test_custom_dedup_window(self):
        """Test custom dedup window via environment variable."""
        with patch.dict(os.environ, {"REMOTE_SESSION_DEDUP_WINDOW_SECONDS": "10"}):
            from importlib import reload

            import app.routes.remote as remote_module

            reload(remote_module)
            assert remote_module.REMOTE_SESSION_DEDUP_WINDOW_SECONDS == 10
