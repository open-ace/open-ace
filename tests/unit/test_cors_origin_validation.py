"""Unit tests for CORS origin validation and normalization."""

import pytest

from app.__init__ import (
    _build_cors_origins_cache,
    _get_allowed_cors_origins,
    _is_allowed_cors_origin,
    _normalize_origin,
)


def test_normalizes_origin_with_trailing_slash():
    """Test that trailing slash is removed and port is added."""
    normalized = _normalize_origin("https://example.com/")
    assert normalized == "https://example.com:443"


def test_normalizes_origin_with_implicit_port():
    """Test that implicit port is inferred from scheme."""
    # HTTPS should default to 443
    assert _normalize_origin("https://example.com") == "https://example.com:443"
    # HTTP should default to 80
    assert _normalize_origin("http://example.com") == "http://example.com:80"


def test_rejects_non_http_origin():
    """Test that non-HTTP(S) origins are rejected."""
    assert _normalize_origin("file:///path") is None
    assert _normalize_origin("ftp://example.com") is None
    assert _normalize_origin("javascript:alert(1)") is None


def test_normalizes_case_and_trailing_dot():
    """Test that hostname case and trailing dot are normalized."""
    # Uppercase should be normalized to lowercase
    assert _normalize_origin("https://Example.COM:443") == "https://example.com:443"
    # Trailing dot should be removed
    assert _normalize_origin("https://example.com.") == "https://example.com:443"


def test_caches_origins_at_startup(monkeypatch):
    """Test that CORS origins are cached and not re-parsed."""
    monkeypatch.setenv("OPENACE_CORS_ALLOWED_ORIGINS", "https://example.com,https://test.com")

    # First call builds cache
    first_call = _get_allowed_cors_origins()

    # Second call should return same object (cached)
    second_call = _get_allowed_cors_origins()

    assert first_call is second_call


def test_matches_normalized_origins(monkeypatch):
    """Test that origins match after normalization."""
    monkeypatch.setenv("OPENACE_CORS_ALLOWED_ORIGINS", "https://Example.COM")

    # Rebuild cache with new env
    cache = _build_cors_origins_cache()

    # Should match normalized form
    assert "https://example.com:443" in cache

    # Should match case-insensitive and with implicit port
    assert _is_allowed_cors_origin("https://example.com")
    assert _is_allowed_cors_origin("https://Example.COM:443")


def test_config_migration_guidance(monkeypatch, caplog):
    """Test that config migration warnings are logged."""
    monkeypatch.setenv("OPENACE_CORS_ALLOWED_ORIGINS", "https://Example.COM,file:///path")

    # Build cache (triggers warnings)
    _build_cors_origins_cache()

    # Check for normalization warning
    assert "normalized" in caplog.text.lower() or "example.com" in caplog.text.lower()


def test_rejects_invalid_schemes_in_config(monkeypatch):
    """Test that invalid schemes in config are rejected."""
    monkeypatch.setenv("OPENACE_CORS_ALLOWED_ORIGINS", "file:///path,http://valid.com")

    cache = _build_cors_origins_cache()

    # file:///path should be rejected
    assert "file:///path" not in cache
    # http://valid.com should be accepted (normalized)
    assert "http://valid.com:80" in cache