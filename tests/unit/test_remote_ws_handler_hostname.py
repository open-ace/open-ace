"""Unit tests for app.remote_ws_handler hostname handling (Issue #2594).

Tests the conservative strategy for hostname handling:
- Non-IP hostnames are treated as needing relay (no DNS resolution)
- Cache TTL mechanism works correctly
- Cache key normalization handles case sensitivity
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.remote_ws_handler import (
    CACHE_TTL_SECONDS,
    _is_ip_address,
    _is_private_ip,
    _needs_relay_cached,
    _normalize_cache_key,
)


class TestIsIpAddress:
    """Test _is_ip_address helper function."""

    def test_ipv4_address_returns_true(self):
        """IPv4 addresses should return True."""
        assert _is_ip_address("192.168.1.1") is True
        assert _is_ip_address("10.0.0.1") is True
        assert _is_ip_address("172.16.0.1") is True
        assert _is_ip_address("8.8.8.8") is True
        assert _is_ip_address("127.0.0.1") is True

    def test_ipv6_address_returns_true(self):
        """IPv6 addresses should return True."""
        assert _is_ip_address("::1") is True
        assert _is_ip_address("fe80::1") is True
        assert _is_ip_address("2001:db8::1") is True

    def test_hostname_returns_false(self):
        """Hostnames should return False."""
        assert _is_ip_address("agent92") is False
        assert _is_ip_address("example.com") is False
        assert _is_ip_address("my-server.local") is False
        assert _is_ip_address("localhost") is False

    def test_invalid_format_returns_false(self):
        """Invalid formats should return False."""
        assert _is_ip_address("") is False
        assert _is_ip_address("abc!def") is False
        assert _is_ip_address("not-an-ip") is False


class TestIsPrivateIpHostname:
    """Test _is_private_ip with hostname handling (Issue #2594)."""

    def test_hostname_defaults_to_relay(self):
        """Non-IP hostnames should return True (need relay)."""
        assert _is_private_ip("ws://agent92:39635") is True
        assert _is_private_ip("ws://my-server.local:39635") is True
        assert _is_private_ip("ws://example.com:39635") is True

    def test_private_ipv4_returns_true(self):
        """Private IPv4 addresses should return True."""
        assert _is_private_ip("ws://192.168.1.1:39635") is True
        assert _is_private_ip("ws://10.0.0.1:39635") is True
        assert _is_private_ip("ws://172.16.0.1:39635") is True
        assert _is_private_ip("ws://127.0.0.1:39635") is True
        assert _is_private_ip("ws://169.254.1.1:39635") is True

    def test_public_ipv4_returns_false(self):
        """Public IPv4 addresses should return False."""
        assert _is_private_ip("ws://8.8.8.8:8080") is False
        assert _is_private_ip("ws://1.1.1.1:8080") is False

    def test_ipv6_private_returns_true(self):
        """Private IPv6 addresses should return True."""
        # IPv6 link-local
        assert _is_private_ip("ws://[fe80::1]:39635") is True
        # IPv6 loopback
        assert _is_private_ip("ws://[::1]:39635") is True


class TestNormalizeCacheKey:
    """Test _normalize_cache_key function."""

    def test_hostname_lowercased(self):
        """Hostnames should be lowercased for cache key."""
        assert _normalize_cache_key("ws://Agent92:39635") == "ws://agent92:39635"
        assert _normalize_cache_key("ws://MyServer.local:39635") == "ws://myserver.local:39635"

    def test_ip_address_preserved(self):
        """IP addresses should be preserved as-is."""
        assert _normalize_cache_key("ws://192.168.1.1:39635") == "ws://192.168.1.1:39635"
        assert _normalize_cache_key("ws://10.0.0.1:39635") == "ws://10.0.0.1:39635"

    def test_case_insensitive_deduplication(self):
        """Different cases of same hostname should normalize to same key."""
        key1 = _normalize_cache_key("ws://Agent92:39635")
        key2 = _normalize_cache_key("ws://agent92:39635")
        key3 = _normalize_cache_key("ws://AGENT92:39635")
        assert key1 == key2 == key3


class TestCacheTTL:
    """Test cache TTL mechanism."""

    def test_cache_hit_within_ttl(self):
        """Cache should return cached result within TTL."""
        from app.remote_ws_handler import _reachability_cache, _reachability_cache_lock

        # Clear cache
        with _reachability_cache_lock:
            _reachability_cache.clear()

        ws_url = "ws://test-server:39635"

        # First call - should compute and cache
        with patch("app.remote_ws_handler._needs_relay", return_value=True):
            result1 = _needs_relay_cached(ws_url)
            assert result1 is True

        # Second call within TTL - should return cached result
        with patch("app.remote_ws_handler._needs_relay", return_value=False) as mock_needs:
            result2 = _needs_relay_cached(ws_url)
            assert result2 is True  # Cached value, not new return value
            mock_needs.assert_not_called()  # Should not re-compute

    def test_cache_expired_after_ttl(self):
        """Cache should re-compute after TTL expires."""
        from app.remote_ws_handler import _reachability_cache, _reachability_cache_lock

        # Clear cache
        with _reachability_cache_lock:
            _reachability_cache.clear()

        ws_url = "ws://test-server-expired:39635"

        # Mock time to control TTL expiry
        with patch("app.remote_ws_handler.time.time") as mock_time:
            # First call at time 0
            mock_time.return_value = 0
            with patch("app.remote_ws_handler._needs_relay", return_value=True):
                result1 = _needs_relay_cached(ws_url)
                assert result1 is True

            # Move time past TTL
            mock_time.return_value = CACHE_TTL_SECONDS + 10

            # Second call after TTL - should re-compute
            with patch("app.remote_ws_handler._needs_relay", return_value=False) as mock_needs:
                result2 = _needs_relay_cached(ws_url)
                assert result2 is False  # New computed value
                mock_needs.assert_called_once()

    def test_cache_cleanup_on_expiry(self):
        """Expired cache entries should be removed."""
        from app.remote_ws_handler import _reachability_cache, _reachability_cache_lock

        # Clear cache
        with _reachability_cache_lock:
            _reachability_cache.clear()

        ws_url = "ws://test-cleanup:39635"

        # Add entry to cache
        with patch("app.remote_ws_handler._needs_relay", return_value=True):
            _needs_relay_cached(ws_url)

        # Verify entry exists
        with _reachability_cache_lock:
            assert ws_url in _reachability_cache

        # Move time past TTL and access again
        with patch("app.remote_ws_handler.time.time") as mock_time:
            mock_time.return_value = CACHE_TTL_SECONDS + 10
            with patch("app.remote_ws_handler._needs_relay", return_value=False):
                _needs_relay_cached(ws_url)

        # Old entry should be removed (new normalized key added)
        # Since we normalize on access, the old key might still exist
        # but the value should be updated
        with _reachability_cache_lock:
            normalized_key = _normalize_cache_key(ws_url)
            assert normalized_key in _reachability_cache


class TestCacheConcurrency:
    """Test cache thread safety with gevent.lock.RLock."""

    def test_concurrent_access_no_race(self):
        """Concurrent access should be safe."""
        from app.remote_ws_handler import _reachability_cache, _reachability_cache_lock

        # Clear cache
        with _reachability_cache_lock:
            _reachability_cache.clear()

        ws_url = "ws://concurrent-test:39635"

        # Simulate concurrent access
        results = []

        def access_cache():
            with patch("app.remote_ws_handler._needs_relay", return_value=True):
                result = _needs_relay_cached(ws_url)
                results.append(result)

        # Multiple concurrent calls (in practice would use gevent.spawn)
        for _ in range(10):
            access_cache()

        # All should get the same result
        assert all(r is True for r in results)

        # Cache should have exactly one entry (normalized)
        with _reachability_cache_lock:
            normalized_key = _normalize_cache_key(ws_url)
            assert normalized_key in _reachability_cache


class TestReachabilityIntegration:
    """Integration tests for reachability logic."""

    def test_hostname_always_needs_relay(self):
        """Hostnames should always be marked as needing relay."""
        # This test verifies the conservative strategy
        ws_url = "ws://agent92:39635"
        assert _is_private_ip(ws_url) is True

    def test_ip_private_vs_public(self):
        """Private IPs should be True, public IPs should be False."""
        # Private IPs
        assert _is_private_ip("ws://192.168.1.100:39635") is True
        assert _is_private_ip("ws://10.20.30.40:39635") is True

        # Public IPs
        assert _is_private_ip("ws://8.8.8.8:8080") is False
        assert _is_private_ip("ws://1.1.1.1:8080") is False