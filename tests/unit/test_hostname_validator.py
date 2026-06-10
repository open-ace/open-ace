"""Unit tests for hostname_validator module."""

import pytest

from app.utils.hostname_validator import (
    is_valid_hostname,
    sanitize_hostname,
)


class TestIsValidHostname:
    """Test hostname validation logic."""

    def test_valid_fqdn(self):
        """Valid fully qualified domain names."""
        assert is_valid_hostname("node75.example.com")
        assert is_valid_hostname("host1.subdomain.domain.com")
        assert is_valid_hostname("server.local")

    def test_valid_short_hostname(self):
        """Valid short hostnames (no dots)."""
        assert is_valid_hostname("buildserver")
        assert is_valid_hostname("db01")
        assert is_valid_hostname("web-server-1")
        assert is_valid_hostname("a")  # Single character

    def test_whitelist_localhost(self):
        """Whitelist entries should always pass."""
        assert is_valid_hostname("localhost")
        assert is_valid_hostname("LOCALHOST")  # Case insensitive

    def test_custom_whitelist(self):
        """Custom whitelist entries should pass."""
        custom_whitelist = {"special-host"}
        assert is_valid_hostname("special-host", whitelist=custom_whitelist)

    def test_filter_hex_8chars(self):
        """Filter 8-character hexadecimal strings."""
        assert not is_valid_hostname("01a73659")
        assert not is_valid_hostname("050c3863")
        assert not is_valid_hostname("abcdef12")

    def test_filter_hex_16chars(self):
        """Filter 16-character hexadecimal strings."""
        assert not is_valid_hostname("01a73659abcdef12")
        assert not is_valid_hostname("1234567890abcdef")

    def test_filter_hex_32chars(self):
        """Filter 32-character hexadecimal strings."""
        assert not is_valid_hostname("01a73659abcdef1201a73659abcdef12")
        assert not is_valid_hostname("1234567890abcdef1234567890abcdef")

    def test_filter_uuid(self):
        """Filter UUID format strings."""
        assert not is_valid_hostname("550e8400-e29b-41d4-a716-446655440000")
        assert not is_valid_hostname("123e4567-e89b-12d3-a456-426614174000")
        assert not is_valid_hostname("550E8400-E29B-41D4-A716-446655440000")  # Uppercase

    def test_filter_pure_numeric_long(self):
        """Filter pure numeric strings with length > 10."""
        assert not is_valid_hostname("12345678901")  # 11 digits
        assert not is_valid_hostname("123456789012")  # 12 digits

    def test_allow_pure_numeric_short(self):
        """Allow pure numeric strings with length <= 10."""
        assert is_valid_hostname("1234567890")  # 10 digits - valid hostname
        assert is_valid_hostname("123")  # 3 digits

    def test_filter_placeholder(self):
        """Filter placeholder format <...>."""
        assert not is_valid_hostname("<HOST_NAME>")
        assert not is_valid_hostname("<hostname>")
        assert not is_valid_hostname("<PLACEHOLDER>")

    def test_filter_empty_and_none(self):
        """Filter empty strings and None."""
        assert not is_valid_hostname("")
        assert not is_valid_hostname(None)

    def test_filter_too_long(self):
        """Filter hostnames exceeding max length."""
        # 254 characters (exceeds RFC 1123 max of 253)
        long_name = "a" * 254
        assert not is_valid_hostname(long_name)

    def test_allow_max_length(self):
        """Allow hostnames at max length."""
        # 253 characters (exactly at RFC 1123 max)
        max_name = "a" * 253
        assert is_valid_hostname(max_name)

    def test_filter_label_too_long(self):
        """Filter hostnames with labels exceeding 63 chars."""
        # Label with 64 chars (exceeds RFC 1123 per-label max)
        long_label = "a" * 64 + ".com"
        assert not is_valid_hostname(long_label)

    def test_filter_invalid_chars(self):
        """Filter hostnames with invalid characters."""
        assert not is_valid_hostname("host_name")  # Underscore not allowed
        assert not is_valid_hostname("host@name")  # @ not allowed
        assert not is_valid_hostname("host!name")  # ! not allowed

    def test_filter_starts_with_hyphen(self):
        """Filter hostnames starting with hyphen."""
        assert not is_valid_hostname("-hostname")
        assert not is_valid_hostname("-host.example.com")

    def test_filter_ends_with_hyphen(self):
        """Filter hostnames ending with hyphen."""
        assert not is_valid_hostname("hostname-")
        assert not is_valid_hostname("host.example.com-")

    def test_filter_starts_with_dot(self):
        """Filter hostnames starting with dot."""
        assert not is_valid_hostname(".hostname")
        assert not is_valid_hostname(".host.example.com")

    def test_filter_ends_with_dot(self):
        """Filter hostnames ending with dot."""
        assert not is_valid_hostname("hostname.")
        assert not is_valid_hostname("host.example.com.")

    def test_allow_hyphen_in_middle(self):
        """Allow hyphens in the middle of hostname."""
        assert is_valid_hostname("web-server-1")
        assert is_valid_hostname("db-server.example.com")


class TestSanitizeHostname:
    """Test hostname sanitization logic."""

    def test_return_valid_hostname(self):
        """Return unchanged valid hostname."""
        result = sanitize_hostname("valid-host.example.com")
        assert result == "valid-host.example.com"

    def test_return_empty_for_invalid(self):
        """Return empty string for invalid hostname."""
        result = sanitize_hostname("01a73659")
        assert result == ""

    def test_return_empty_for_none(self):
        """Return empty string for None."""
        result = sanitize_hostname(None)
        assert result == ""

    def test_return_empty_for_empty_string(self):
        """Return empty string for empty input."""
        result = sanitize_hostname("")
        assert result == ""

    def test_return_empty_for_placeholder(self):
        """Return empty string for placeholder format."""
        result = sanitize_hostname("<HOST_NAME>")
        assert result == ""

    def test_custom_whitelist(self):
        """Custom whitelist should allow special hostnames."""
        custom_whitelist = {"special-host"}
        result = sanitize_hostname("special-host", whitelist=custom_whitelist)
        assert result == "special-host"

    def test_log_warnings_false(self):
        """No warnings logged when log_warnings=False."""
        # This should not raise exception or log anything
        result = sanitize_hostname("01a73659", log_warnings=False)
        assert result == ""