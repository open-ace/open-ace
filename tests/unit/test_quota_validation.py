"""
Unit tests for quota validation schema.

Tests validation of quota values including:
- Token quotas (in M units)
- Request quotas (actual count)
- Range validation
- Edge cases (NaN, negative, exceeding limits)
"""

import pytest
from app.schemas.quota import (
    validate_token_quota,
    validate_request_quota,
    validate_quota_update,
    get_quota_limits,
    MAX_TOKEN_QUOTA,
    MAX_REQUEST_QUOTA,
    MIN_QUOTA,
)


class TestValidateTokenQuota:
    """Test token quota validation."""

    def test_none_value_is_valid(self):
        """None (unlimited) should be valid."""
        is_valid, error = validate_token_quota(None, "daily_token_quota")
        assert is_valid is True
        assert error == ""

    def test_zero_value_is_valid(self):
        """Zero should be valid."""
        is_valid, error = validate_token_quota(0, "daily_token_quota")
        assert is_valid is True
        assert error == ""

    def test_positive_value_within_limit(self):
        """Positive value within limit should be valid."""
        is_valid, error = validate_token_quota(100, "daily_token_quota")
        assert is_valid is True
        assert error == ""

    def test_value_at_max_limit(self):
        """Value at max limit should be valid."""
        is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA, "daily_token_quota")
        assert is_valid is True
        assert error == ""

    def test_negative_value_invalid(self):
        """Negative value should be invalid."""
        is_valid, error = validate_token_quota(-1, "daily_token_quota")
        assert is_valid is False
        assert "cannot be negative" in error

    def test_value_exceeding_max_invalid(self):
        """Value exceeding max limit should be invalid."""
        is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA + 1, "daily_token_quota")
        assert is_valid is False
        assert "exceeds maximum limit" in error

    def test_large_value_exceeding_max(self):
        """Large value like 1e21 should be invalid."""
        # Note: Python handles large numbers better than JavaScript
        # but we should still reject values exceeding database limit
        large_value = 10**21
        is_valid, error = validate_token_quota(large_value, "daily_token_quota")
        assert is_valid is False
        assert "exceeds maximum limit" in error

    def test_float_converted_to_int(self):
        """Float should be converted to int."""
        is_valid, error = validate_token_quota(100.5, "daily_token_quota")
        # Should convert to int and validate
        assert is_valid is True
        assert error == ""

    def test_custom_quota_name(self):
        """Custom quota name should appear in error message."""
        is_valid, error = validate_token_quota(-1, "monthly_token_quota")
        assert is_valid is False
        assert "monthly_token_quota" in error


class TestValidateRequestQuota:
    """Test request quota validation."""

    def test_none_value_is_valid(self):
        """None (unlimited) should be valid."""
        is_valid, error = validate_request_quota(None, "daily_request_quota")
        assert is_valid is True
        assert error == ""

    def test_zero_value_is_valid(self):
        """Zero should be valid."""
        is_valid, error = validate_request_quota(0, "daily_request_quota")
        assert is_valid is True
        assert error == ""

    def test_positive_value_within_limit(self):
        """Positive value within limit should be valid."""
        is_valid, error = validate_request_quota(1000, "daily_request_quota")
        assert is_valid is True
        assert error == ""

    def test_value_at_max_limit(self):
        """Value at max limit should be valid."""
        is_valid, error = validate_request_quota(MAX_REQUEST_QUOTA, "daily_request_quota")
        assert is_valid is True
        assert error == ""

    def test_negative_value_invalid(self):
        """Negative value should be invalid."""
        is_valid, error = validate_request_quota(-1, "daily_request_quota")
        assert is_valid is False
        assert "cannot be negative" in error

    def test_value_exceeding_max_invalid(self):
        """Value exceeding max limit should be invalid."""
        is_valid, error = validate_request_quota(MAX_REQUEST_QUOTA + 1, "daily_request_quota")
        assert is_valid is False
        assert "exceeds maximum limit" in error


class TestValidateQuotaUpdate:
    """Test complete quota update validation."""

    def test_all_fields_valid(self):
        """All valid fields should pass validation."""
        is_valid, errors = validate_quota_update(
            daily_token_quota=100,
            monthly_token_quota=300,
            daily_request_quota=1000,
            monthly_request_quota=30000,
        )
        assert is_valid is True
        assert len(errors) == 0

    def test_all_fields_none(self):
        """All None (unlimited) should pass validation."""
        is_valid, errors = validate_quota_update(
            daily_token_quota=None,
            monthly_token_quota=None,
            daily_request_quota=None,
            monthly_request_quota=None,
        )
        assert is_valid is True
        assert len(errors) == 0

    def test_one_field_invalid(self):
        """One invalid field should fail validation."""
        is_valid, errors = validate_quota_update(
            daily_token_quota=-1,  # Invalid
            monthly_token_quota=300,
            daily_request_quota=1000,
            monthly_request_quota=30000,
        )
        assert is_valid is False
        assert "daily_token_quota" in errors
        assert len(errors) == 1

    def test_multiple_fields_invalid(self):
        """Multiple invalid fields should return multiple errors."""
        is_valid, errors = validate_quota_update(
            daily_token_quota=-1,  # Invalid
            monthly_token_quota=MAX_TOKEN_QUOTA + 100,  # Invalid
            daily_request_quota=-10,  # Invalid
            monthly_request_quota=30000,
        )
        assert is_valid is False
        assert "daily_token_quota" in errors
        assert "monthly_token_quota" in errors
        assert "daily_request_quota" in errors
        assert len(errors) == 3

    def test_partial_update_valid(self):
        """Partial update (some fields None) should be valid."""
        is_valid, errors = validate_quota_update(
            daily_token_quota=100,
            monthly_token_quota=None,  # Not updated
            daily_request_quota=None,  # Not updated
            monthly_request_quota=None,  # Not updated
        )
        assert is_valid is True
        assert len(errors) == 0


class TestGetQuotaLimits:
    """Test quota limits configuration."""

    def test_returns_correct_structure(self):
        """Should return correct configuration structure."""
        limits = get_quota_limits()
        assert "token_quota" in limits
        assert "request_quota" in limits

    def test_token_quota_limits(self):
        """Token quota limits should be correct."""
        limits = get_quota_limits()
        token_limits = limits["token_quota"]
        assert token_limits["min"] == MIN_QUOTA
        assert token_limits["max"] == MAX_TOKEN_QUOTA
        assert token_limits["unit"] == "M"

    def test_request_quota_limits(self):
        """Request quota limits should be correct."""
        limits = get_quota_limits()
        request_limits = limits["request_quota"]
        assert request_limits["min"] == MIN_QUOTA
        assert request_limits["max"] == MAX_REQUEST_QUOTA
        assert request_limits["unit"] == ""


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_very_large_token_quota(self):
        """Very large token quota (simulating 1e21 issue) should be rejected."""
        # Simulate the 1e21 issue from the bug report
        # In JavaScript: parseInt('1.0000000000000003e+21') returns 1
        # In Python, we handle this differently, but should still reject
        huge_value = 10**21  # This is 1 sextillion
        is_valid, error = validate_token_quota(huge_value, "daily_token_quota")
        assert is_valid is False
        assert "exceeds maximum limit" in error

    def test_boundary_values(self):
        """Test values at boundary."""
        # Just below max
        is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA - 1, "daily_token_quota")
        assert is_valid is True

        # Exactly at max
        is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA, "daily_token_quota")
        assert is_valid is True

        # Just above max
        is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA + 1, "daily_token_quota")
        assert is_valid is False

    def test_zero_vs_none_distinction(self):
        """Zero and None should both be valid (different meanings)."""
        # Zero: quota set to 0 (might mean no quota or unlimited depending on UI)
        is_valid_zero, error_zero = validate_token_quota(0, "daily_token_quota")
        assert is_valid_zero is True

        # None: quota not set (unlimited in UI)
        is_valid_none, error_none = validate_token_quota(None, "daily_token_quota")
        assert is_valid_none is True

    def test_string_conversion(self):
        """String input should be converted to int."""
        # This simulates what might happen if frontend sends string
        is_valid, error = validate_token_quota("100", "daily_token_quota")
        assert is_valid is True
        assert error == ""

    def test_invalid_string_input(self):
        """Invalid string should be rejected."""
        is_valid, error = validate_token_quota("invalid", "daily_token_quota")
        assert is_valid is False
        assert "must be an integer" in error