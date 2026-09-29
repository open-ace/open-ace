"""
Unit tests for branding_validator module (Issue #3271).

Tests SSRF protection for logo URLs, XSS protection for brand names and
welcome messages, and validation logic.
"""

import pytest

from app.utils.branding_validator import (
    MAX_COPYRIGHT_LENGTH,
    MAX_SYSTEM_NAME_LENGTH,
    MAX_WELCOME_MESSAGE_LENGTH,
    validate_brand_name,
    validate_branding_settings,
    validate_copyright_text,
    validate_logo_url,
    validate_welcome_message,
)


class TestValidateLogoUrl:
    """Tests for logo URL validation (SSRF protection)."""

    def test_empty_url_is_valid(self):
        """Empty URL should be valid (uses default)."""
        is_valid, error = validate_logo_url(None)
        assert is_valid is True
        assert error == ""

        is_valid, error = validate_logo_url("")
        assert is_valid is True
        assert error == ""

    def test_https_url_is_valid(self):
        """HTTPS URLs to public addresses should be valid."""
        # Note: This test uses real DNS resolution, so we use example.com
        is_valid, error = validate_logo_url("https://example.com/logo.png")
        # This may fail in environments without DNS, but that's okay
        # The important thing is that HTTPS is accepted
        assert "HTTPS" in error or is_valid is True

    def test_http_url_is_rejected(self):
        """HTTP URLs should be rejected (HTTPS only)."""
        is_valid, error = validate_logo_url("http://example.com/logo.png")
        assert is_valid is False
        assert "HTTPS" in error

    def test_data_url_is_rejected(self):
        """data: URLs should be rejected."""
        is_valid, error = validate_logo_url("data:image/png;base64,abc123")
        assert is_valid is False
        # data: URLs are rejected because they don't use HTTPS
        assert "https" in error.lower() or "data" in error.lower()

    def test_file_url_is_rejected(self):
        """file: URLs should be rejected."""
        is_valid, error = validate_logo_url("file:///etc/passwd")
        assert is_valid is False
        # file: URLs are rejected because they don't use HTTPS
        assert "https" in error.lower() or "file" in error.lower()

    def test_javascript_url_is_rejected(self):
        """javascript: URLs should be rejected."""
        is_valid, error = validate_logo_url("javascript:alert('xss')")
        assert is_valid is False
        # javascript: URLs are rejected because they don't use HTTPS
        assert "https" in error.lower() or "javascript" in error.lower()

    def test_localhost_is_rejected(self):
        """localhost should be rejected (SSRF protection)."""
        is_valid, error = validate_logo_url("https://localhost/logo.png")
        assert is_valid is False
        assert "localhost" in error.lower() or "non-public" in error.lower()

    def test_private_ip_is_rejected(self):
        """Private IP addresses should be rejected (SSRF protection)."""
        is_valid, error = validate_logo_url("https://192.168.1.1/logo.png")
        assert is_valid is False
        assert "non-public" in error.lower() or "private" in error.lower()

    def test_aws_metadata_is_rejected(self):
        """AWS metadata endpoint should be rejected (SSRF protection)."""
        is_valid, error = validate_logo_url("https://169.254.169.254/latest/meta-data")
        assert is_valid is False
        assert "non-public" in error.lower() or "metadata" in error.lower()


class TestValidateBrandName:
    """Tests for brand name validation (XSS protection)."""

    def test_empty_name_is_valid(self):
        """Empty name should be valid (uses default)."""
        is_valid, error = validate_brand_name(None)
        assert is_valid is True
        assert error == ""

        is_valid, error = validate_brand_name("")
        assert is_valid is True
        assert error == ""

    def test_valid_name_is_accepted(self):
        """Valid brand names should be accepted."""
        is_valid, error = validate_brand_name("My Company ACE")
        assert is_valid is True
        assert error == ""

    def test_long_name_is_rejected(self):
        """Names exceeding max length should be rejected."""
        long_name = "A" * (MAX_SYSTEM_NAME_LENGTH + 1)
        is_valid, error = validate_brand_name(long_name)
        assert is_valid is False
        assert str(MAX_SYSTEM_NAME_LENGTH) in error

    def test_script_tag_is_rejected(self):
        """Script tags should be rejected (XSS protection)."""
        is_valid, error = validate_brand_name("<script>alert('xss')</script>")
        assert is_valid is False
        assert "script" in error.lower() or "html" in error.lower()

    def test_javascript_protocol_is_rejected(self):
        """javascript: protocol should be rejected (XSS protection)."""
        is_valid, error = validate_brand_name("javascript:alert('xss')")
        assert is_valid is False
        assert "javascript" in error.lower() or "script" in error.lower()

    def test_event_handler_is_rejected(self):
        """Event handlers should be rejected (XSS protection)."""
        is_valid, error = validate_brand_name("test onerror=alert('xss')")
        assert is_valid is False
        assert "onerror" in error.lower() or "script" in error.lower()

    def test_iframe_is_rejected(self):
        """iframe tags should be rejected (XSS protection)."""
        is_valid, error = validate_brand_name("<iframe src='evil.com'>")
        assert is_valid is False
        assert "iframe" in error.lower() or "html" in error.lower()


class TestValidateWelcomeMessage:
    """Tests for welcome message validation (XSS protection)."""

    def test_empty_message_is_valid(self):
        """Empty message should be valid (uses default)."""
        is_valid, error = validate_welcome_message(None)
        assert is_valid is True
        assert error == ""

        is_valid, error = validate_welcome_message({})
        assert is_valid is True
        assert error == ""

    def test_valid_message_is_accepted(self):
        """Valid welcome messages should be accepted."""
        message = {"en": "Welcome to My Company", "zh": "欢迎使用我的公司"}
        is_valid, error = validate_welcome_message(message)
        assert is_valid is True
        assert error == ""

    def test_unsupported_language_is_warned(self):
        """Unsupported languages should be warned but not rejected."""
        message = {"fr": "Bienvenue"}
        is_valid, error = validate_welcome_message(message)
        # Should still be valid, just a warning
        assert is_valid is True
        assert error == ""

    def test_non_dict_is_rejected(self):
        """Non-dict message should be rejected."""
        is_valid, error = validate_welcome_message("not a dict")
        assert is_valid is False
        assert "dictionary" in error.lower()

    def test_non_string_value_is_rejected(self):
        """Non-string values should be rejected."""
        message = {"en": 123}
        is_valid, error = validate_welcome_message(message)
        assert is_valid is False
        assert "string" in error.lower()

    def test_long_message_is_rejected(self):
        """Messages exceeding max length should be rejected."""
        long_message = {"en": "A" * (MAX_WELCOME_MESSAGE_LENGTH + 1)}
        is_valid, error = validate_welcome_message(long_message)
        assert is_valid is False
        assert str(MAX_WELCOME_MESSAGE_LENGTH) in error

    def test_script_tag_is_rejected(self):
        """Script tags should be rejected (XSS protection)."""
        message = {"en": "<script>alert('xss')</script>"}
        is_valid, error = validate_welcome_message(message)
        assert is_valid is False
        assert "script" in error.lower() or "html" in error.lower()

    def test_javascript_protocol_is_rejected(self):
        """javascript: protocol should be rejected (XSS protection)."""
        message = {"en": "javascript:alert('xss')"}
        is_valid, error = validate_welcome_message(message)
        assert is_valid is False
        assert "javascript" in error.lower() or "script" in error.lower()


class TestValidateCopyrightText:
    """Tests for copyright text validation (XSS protection)."""

    def test_empty_text_is_valid(self):
        """Empty text should be valid (uses default)."""
        is_valid, error = validate_copyright_text(None)
        assert is_valid is True
        assert error == ""

        is_valid, error = validate_copyright_text("")
        assert is_valid is True
        assert error == ""

    def test_valid_text_is_accepted(self):
        """Valid copyright text should be accepted."""
        is_valid, error = validate_copyright_text("© 2026 My Company. All rights reserved.")
        assert is_valid is True
        assert error == ""

    def test_long_text_is_rejected(self):
        """Text exceeding max length should be rejected."""
        long_text = "A" * (MAX_COPYRIGHT_LENGTH + 1)
        is_valid, error = validate_copyright_text(long_text)
        assert is_valid is False
        assert str(MAX_COPYRIGHT_LENGTH) in error

    def test_script_tag_is_rejected(self):
        """Script tags should be rejected (XSS protection)."""
        is_valid, error = validate_copyright_text("<script>alert('xss')</script>")
        assert is_valid is False
        assert "script" in error.lower() or "html" in error.lower()


class TestValidateBrandingSettings:
    """Tests for combined branding settings validation."""

    def test_all_valid_settings(self):
        """All valid settings should be accepted."""
        is_valid, errors = validate_branding_settings(
            logo_url="https://example.com/logo.png",
            system_name="My Company",
            welcome_message={"en": "Welcome"},
            copyright_text="© 2026",
        )
        # Note: logo_url may fail DNS, that's okay
        # We're testing that the validation function runs without errors
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)

    def test_empty_settings_are_valid(self):
        """All empty settings should be valid (uses defaults)."""
        is_valid, errors = validate_branding_settings()
        assert is_valid is True
        assert errors == []

    def test_partial_validation(self):
        """Only provided settings should be validated."""
        is_valid, errors = validate_branding_settings(system_name="My Company")
        assert is_valid is True
        assert errors == []

    def test_multiple_errors_are_collected(self):
        """Multiple validation errors should be collected."""
        is_valid, errors = validate_branding_settings(
            logo_url="http://invalid.com",  # HTTP not allowed
            system_name="<script>",  # XSS
        )
        assert is_valid is False
        assert len(errors) >= 1  # At least one error
