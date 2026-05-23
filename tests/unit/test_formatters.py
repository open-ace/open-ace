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

    @pytest.mark.parametrize(
        "user_data,expected_active",
        [
            ({"id": 1, "username": "testuser"}, True),
            ({"id": 1, "username": "testuser", "is_active": False}, False),
        ],
        ids=["default_is_active", "explicit_inactive"],
    )
    def test_user_data_is_active(self, user_data, expected_active):
        result = format_user_data(user_data)
        assert result["is_active"] is expected_active

    def test_empty_user_data(self):
        result = format_user_data({})
        assert result["id"] is None
        assert result["is_active"] is True


class TestFormatTimestamp:
    """Test format_timestamp function."""

    @pytest.mark.parametrize(
        "input_ts,expected,check_contains",
        [
            ("2026-01-15T10:30:00Z", "2026-01-15 10:30:00", None),
            ("2026-01-15T10:30:00+08:00", "2026-01-15 10:30:00", None),
            (None, None, None),
            ("", None, None),
            ("not-a-timestamp", "not-a-timestamp", None),
            ("2026-01-15", "2026-01-15 00:00:00", None),
        ],
        ids=[
            "iso_with_z",
            "iso_with_offset",
            "none_input",
            "empty_string",
            "invalid_timestamp",
            "plain_date",
        ],
    )
    def test_format_timestamp(self, input_ts, expected, check_contains):
        result = format_timestamp(input_ts)
        assert result == expected

    def test_iso_format_with_milliseconds(self):
        result = format_timestamp("2026-01-15T10:30:00.123456Z")
        assert "2026-01-15" in result
        assert "10:30:00" in result


class TestFormatNumber:
    """Test format_number function."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0"),
            (42, "42"),
            (1000, "1,000"),
            (1000000, "1,000,000"),
            (1234567, "1,234,567"),
            (-1000, "-1,000"),
        ],
        ids=["zero", "small", "thousands", "millions", "mixed_digits", "negative"],
    )
    def test_format_number(self, value, expected):
        assert format_number(value) == expected


class TestFormatPercentage:
    """Test format_percentage function."""

    @pytest.mark.parametrize(
        "value,decimals,expected",
        [
            (85.5, None, "85.5%"),
            (85.567, 0, "86%"),
            (85.567, 2, "85.57%"),
            (0.0, None, "0.0%"),
            (100.0, None, "100.0%"),
            (-5.5, None, "-5.5%"),
            (1234.5678, 3, "1234.568%"),
        ],
        ids=[
            "default_decimals",
            "zero_decimals",
            "two_decimals",
            "zero_value",
            "100_percent",
            "negative",
            "large_value",
        ],
    )
    def test_format_percentage(self, value, decimals, expected):
        if decimals is None:
            assert format_percentage(value) == expected
        else:
            assert format_percentage(value, decimals=decimals) == expected
