"""Unit tests for formatters module."""

from unittest.mock import MagicMock

import pytest

from app.utils.formatters import (
    format_message_data,
    format_number,
    format_percentage,
    format_timestamp,
    format_usage_data,
    format_user_data,
)


class TestFormatUsageData:
    """Test format_usage_data function."""

    def test_full_usage_data(self):
        usage = {
            "date": "2026-01-15",
            "tool_name": "qwen-code",
            "host_name": "host1",
            "tokens_used": 5000,
            "input_tokens": 3000,
            "output_tokens": 2000,
            "cache_tokens": 100,
            "request_count": 50,
            "models_used": "gpt-4",
        }
        result = format_usage_data(usage)
        assert result["date"] == "2026-01-15"
        assert result["tool_name"] == "qwen-code"
        assert result["host_name"] == "host1"
        assert result["tokens_used"] == 5000
        assert result["input_tokens"] == 3000
        assert result["output_tokens"] == 2000
        assert result["cache_tokens"] == 100
        assert result["request_count"] == 50
        assert result["models_used"] == "gpt-4"

    def test_minimal_usage_data(self):
        usage = {"date": "2026-01-15"}
        result = format_usage_data(usage)
        assert result["date"] == "2026-01-15"
        assert result["tokens_used"] == 0
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["cache_tokens"] == 0
        assert result["request_count"] == 0
        assert result["models_used"] is None

    def test_empty_usage_data(self):
        result = format_usage_data({})
        assert result["tokens_used"] == 0
        assert result["request_count"] == 0

    def test_usage_data_with_none_values(self):
        usage = {"date": None, "tool_name": None}
        result = format_usage_data(usage)
        assert result["date"] is None
        assert result["tool_name"] is None
        assert result["tokens_used"] == 0


class TestFormatMessageData:
    """Test format_message_data function."""

    def test_full_message_data(self):
        message = {
            "id": 1,
            "date": "2026-01-15",
            "tool_name": "qwen-code",
            "host_name": "host1",
            "message_id": "msg-123",
            "role": "user",
            "content": "Hello",
            "tokens_used": 100,
            "model": "gpt-4",
            "timestamp": "2026-01-15T10:00:00",
            "sender_name": "user1",
        }
        result = format_message_data(message)
        assert result["id"] == 1
        assert result["date"] == "2026-01-15"
        assert result["role"] == "user"
        assert result["content"] == "Hello"
        assert result["tokens_used"] == 100
        assert result["model"] == "gpt-4"
        assert result["sender_name"] == "user1"

    def test_minimal_message_data(self):
        message = {"id": 1}
        result = format_message_data(message)
        assert result["id"] == 1
        assert result["tokens_used"] == 0

    def test_empty_message_data(self):
        result = format_message_data({})
        assert result["tokens_used"] == 0
        assert result["id"] is None


class TestFormatUserData:
    """Test format_user_data function."""

    def test_full_user_data_without_sensitive(self):
        user = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "role": "admin",
            "is_active": True,
            "created_at": "2026-01-01",
            "last_login": "2026-01-15",
            "daily_token_quota": 1000000,
            "monthly_token_quota": 30000000,
        }
        result = format_user_data(user, include_sensitive=False)
        assert result["id"] == 1
        assert result["username"] == "testuser"
        assert result["email"] == "test@example.com"
        assert result["role"] == "admin"
        assert result["is_active"] is True
        assert "daily_token_quota" not in result
        assert "monthly_token_quota" not in result

    def test_user_data_with_sensitive(self):
        user = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "role": "user",
            "is_active": True,
            "created_at": "2026-01-01",
            "last_login": "2026-01-15",
            "daily_token_quota": 1000000,
            "monthly_token_quota": 30000000,
        }
        result = format_user_data(user, include_sensitive=True)
        assert result["daily_token_quota"] == 1000000
        assert result["monthly_token_quota"] == 30000000

    def test_user_data_default_is_active(self):
        user = {"id": 1, "username": "testuser"}
        result = format_user_data(user)
        assert result["is_active"] is True

    def test_user_data_explicit_inactive(self):
        user = {"id": 1, "username": "testuser", "is_active": False}
        result = format_user_data(user)
        assert result["is_active"] is False

    def test_empty_user_data(self):
        result = format_user_data({})
        assert result["id"] is None
        assert result["is_active"] is True


class TestFormatTimestamp:
    """Test format_timestamp function."""

    def test_iso_format_with_z(self):
        result = format_timestamp("2026-01-15T10:30:00Z")
        assert result == "2026-01-15 10:30:00"

    def test_iso_format_with_offset(self):
        result = format_timestamp("2026-01-15T10:30:00+08:00")
        assert result == "2026-01-15 10:30:00"

    def test_none_input(self):
        result = format_timestamp(None)
        assert result is None

    def test_empty_string(self):
        result = format_timestamp("")
        assert result is None

    def test_invalid_timestamp(self):
        result = format_timestamp("not-a-timestamp")
        assert result == "not-a-timestamp"

    def test_plain_date_string(self):
        result = format_timestamp("2026-01-15")
        assert result == "2026-01-15 00:00:00"

    def test_iso_format_with_milliseconds(self):
        result = format_timestamp("2026-01-15T10:30:00.123456Z")
        assert "2026-01-15" in result
        assert "10:30:00" in result


class TestFormatNumber:
    """Test format_number function."""

    def test_zero(self):
        assert format_number(0) == "0"

    def test_small_number(self):
        assert format_number(42) == "42"

    def test_thousands(self):
        assert format_number(1000) == "1,000"

    def test_millions(self):
        assert format_number(1000000) == "1,000,000"

    def test_mixed_digits(self):
        assert format_number(1234567) == "1,234,567"

    def test_negative_number(self):
        assert format_number(-1000) == "-1,000"


class TestFormatPercentage:
    """Test format_percentage function."""

    def test_default_decimals(self):
        assert format_percentage(85.5) == "85.5%"

    def test_zero_decimals(self):
        assert format_percentage(85.567, decimals=0) == "86%"

    def test_two_decimals(self):
        assert format_percentage(85.567, decimals=2) == "85.57%"

    def test_zero_value(self):
        assert format_percentage(0.0) == "0.0%"

    def test_100_percent(self):
        assert format_percentage(100.0) == "100.0%"

    def test_negative_percentage(self):
        assert format_percentage(-5.5) == "-5.5%"

    def test_large_value(self):
        result = format_percentage(1234.5678, decimals=3)
        assert result == "1234.568%"
