"""
Unit tests for Rate Limit Middleware.

Tests for rate limiting functionality including multi-process support.
"""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.middleware.rate_limit import (
    DatabaseRateLimiterBackend,
    InMemoryRateLimiterBackend,
    RateLimiter,
    RedisRateLimiterBackend,
    get_rate_limit_headers,
    rate_limit,
)


class TestInMemoryRateLimiterBackend:
    """Tests for in-memory rate limiter backend."""

    def test_allows_requests_under_limit(self):
        """Test that requests under limit are allowed."""
        backend = InMemoryRateLimiterBackend()
        key = "test:user:api"

        # First 5 requests should be allowed
        for _ in range(5):
            assert backend.is_allowed(key, max_requests=10, window=60)

    def test_blocks_requests_over_limit(self):
        """Test that requests over limit are blocked."""
        backend = InMemoryRateLimiterBackend()
        key = "test:user:api"

        # Make 10 requests (limit)
        for _ in range(10):
            backend.is_allowed(key, max_requests=10, window=60)

        # 11th request should be blocked
        assert not backend.is_allowed(key, max_requests=10, window=60)

    def test_get_remaining_returns_correct_count(self):
        """Test that remaining count is correct."""
        backend = InMemoryRateLimiterBackend()
        key = "test:user:api"

        # Make 3 requests
        for _ in range(3):
            backend.is_allowed(key, max_requests=10, window=60)

        remaining = backend.get_remaining(key, max_requests=10, window=60)
        assert remaining == 7

    def test_window_expiry_allows_new_requests(self):
        """Test that expired requests are cleaned up."""
        backend = InMemoryRateLimiterBackend()
        key = "test:user:api"

        # Make requests
        for _ in range(10):
            backend.is_allowed(key, max_requests=10, window=1)

        # Wait for window to expire
        time.sleep(1.5)

        # New requests should be allowed
        assert backend.is_allowed(key, max_requests=10, window=1)


class TestRedisRateLimiterBackend:
    """Tests for Redis rate limiter backend."""

    def test_is_allowed_under_limit(self):
        """Test that requests under limit are allowed."""
        mock_redis = Mock()
        mock_redis.eval.return_value = 1  # Allowed

        backend = RedisRateLimiterBackend(mock_redis)
        key = "test:user:api"

        assert backend.is_allowed(key, max_requests=10, window=60)
        assert mock_redis.eval.called

    def test_is_allowed_over_limit(self):
        """Test that requests over limit are blocked."""
        mock_redis = Mock()
        mock_redis.eval.return_value = 0  # Blocked

        backend = RedisRateLimiterBackend(mock_redis)
        key = "test:user:api"

        assert not backend.is_allowed(key, max_requests=10, window=60)

    def test_get_remaining_count(self):
        """Test that remaining count is returned correctly."""
        mock_redis = Mock()
        mock_redis.zcard.return_value = 3

        backend = RedisRateLimiterBackend(mock_redis)
        remaining = backend.get_remaining("test:key", max_requests=10, window=60)

        assert remaining == 7


class TestDatabaseRateLimiterBackend:
    """Tests for database rate limiter backend."""

    def test_is_allowed_under_limit(self):
        """Test that requests under limit are allowed."""
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = [3]  # Current count

        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        backend = DatabaseRateLimiterBackend(get_connection_func=lambda: mock_conn)
        key = "test:user:api"

        assert backend.is_allowed(key, max_requests=10, window=60)
        assert mock_cursor.execute.called

    def test_is_allowed_over_limit(self):
        """Test that requests over limit are blocked."""
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = [10]  # At limit

        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        backend = DatabaseRateLimiterBackend(get_connection_func=lambda: mock_conn)
        key = "test:user:api"

        assert not backend.is_allowed(key, max_requests=10, window=60)

    def test_fallback_on_error(self):
        """Test that errors fall back to allowing requests."""
        backend = DatabaseRateLimiterBackend(get_connection_func=lambda: None)
        backend._get_connection = Mock(side_effect=Exception("DB error"))

        # Should allow request on error
        assert backend.is_allowed("test:key", max_requests=10, window=60)


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_singleton_pattern(self):
        """Test that RateLimiter uses singleton pattern."""
        RateLimiter.reset_instance()

        limiter1 = RateLimiter()
        limiter2 = RateLimiter()

        assert limiter1 is limiter2

        RateLimiter.reset_instance()

    def test_uses_provided_backend(self):
        """Test that RateLimiter uses provided backend."""
        RateLimiter.reset_instance()

        mock_backend = Mock()
        mock_backend.is_allowed.return_value = True

        limiter = RateLimiter(backend=mock_backend)
        limiter.is_allowed("test:key", max_requests=10, window=60)

        assert mock_backend.is_allowed.called

        RateLimiter.reset_instance()

    def test_delegates_to_backend(self):
        """Test that RateLimiter delegates to backend."""
        RateLimiter.reset_instance()

        mock_backend = Mock()
        mock_backend.is_allowed.return_value = True
        mock_backend.get_remaining.return_value = 5

        limiter = RateLimiter(backend=mock_backend)

        limiter.is_allowed("test:key", max_requests=10, window=60)
        assert mock_backend.is_allowed.called

        limiter.get_remaining("test:key", max_requests=10, window=60)
        assert mock_backend.get_remaining.called

        RateLimiter.reset_instance()


class TestRateLimitDecorator:
    """Tests for rate_limit decorator."""

    def test_allows_requests_under_limit(self):
        """Test that decorator allows requests under limit."""
        from flask import Flask

        RateLimiter.reset_instance()

        app = Flask(__name__)

        # Setup backend first
        backend = InMemoryRateLimiterBackend()
        limiter = RateLimiter(backend=backend)

        @rate_limit(max_requests=10, window=60)
        def test_endpoint():
            return {"status": "ok"}

        with app.app_context():
            with app.test_request_context():
                # Mock g.user
                from flask import g

                g.user = {"id": "test_user"}

                response = test_endpoint()
                assert response == {"status": "ok"}

        RateLimiter.reset_instance()

    def test_blocks_requests_over_limit(self):
        """Test that decorator blocks requests over limit."""
        from flask import Flask

        RateLimiter.reset_instance()

        app = Flask(__name__)

        # Create limiter with in-memory backend BEFORE decorator is applied
        backend = InMemoryRateLimiterBackend()
        limiter = RateLimiter(backend=backend)

        @rate_limit(max_requests=2, window=60)
        def test_endpoint():
            return {"status": "ok"}

        with app.app_context():
            with app.test_request_context():
                from flask import g

                g.user = {"id": "test_user"}

                # First two should succeed
                result1 = test_endpoint()
                assert result1 == {"status": "ok"}
                result2 = test_endpoint()
                assert result2 == {"status": "ok"}

                # Third should be blocked
                result3 = test_endpoint()
                # When rate limited, returns a tuple (error_dict, status_code)
                assert isinstance(result3, tuple), f"Expected tuple, got {type(result3)}: {result3}"
                assert result3[1] == 429
                assert "Rate limit exceeded" in result3[0]["error"]

        RateLimiter.reset_instance()


class TestGetRateLimitHeaders:
    """Tests for get_rate_limit_headers function."""

    def test_returns_headers(self):
        """Test that headers are returned correctly."""
        RateLimiter.reset_instance()

        backend = Mock()
        backend.get_remaining.return_value = 5

        # Create limiter with mock backend - this becomes the singleton
        limiter = RateLimiter(backend=backend)
        # Ensure backend is set
        limiter._backend = backend

        # Now get_rate_limit_headers will use this limiter
        headers = get_rate_limit_headers("test:key", max_requests=10, window=60)

        assert headers["X-RateLimit-Limit"] == "10"
        assert headers["X-RateLimit-Remaining"] == "5"
        assert headers["X-RateLimit-Reset"] == "60"

        RateLimiter.reset_instance()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])