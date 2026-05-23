"""Unit tests for helper utilities."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.utils.helpers import format_tokens, get_date_range, get_days_ago, get_today, parse_date


class TestFormatTokens:
    """Test format_tokens function."""

    def test_zero_tokens(self):
        assert format_tokens(0) == "0"

    def test_small_number(self):
        assert format_tokens(42) == "42"

    def test_hundreds(self):
        assert format_tokens(500) == "500"

    def test_thousands(self):
        result = format_tokens(1500)
        assert result == "1.50K"

    def test_exact_thousand(self):
        result = format_tokens(1000)
        assert result == "1.00K"

    def test_millions(self):
        result = format_tokens(1_500_000)
        assert result == "1.50M"

    def test_exact_million(self):
        result = format_tokens(1_000_000)
        assert result == "1.00M"

    def test_billions(self):
        result = format_tokens(1_500_000_000)
        assert result == "1.50B"

    def test_exact_billion(self):
        result = format_tokens(1_000_000_000)
        assert result == "1.00B"

    def test_large_number_below_billion(self):
        result = format_tokens(999_999_999)
        assert result == "1000.00M"

    def test_boundary_thousand(self):
        result = format_tokens(999)
        assert result == "999"

    def test_boundary_million(self):
        result = format_tokens(999_999)
        assert result == "1000.00K"


class TestParseDate:
    """Test parse_date function."""

    def test_valid_date(self):
        assert parse_date("2026-01-15") == "2026-01-15"

    def test_none_input(self):
        assert parse_date(None) is None

    def test_empty_string(self):
        assert parse_date("") is None

    def test_invalid_format(self):
        assert parse_date("01-15-2026") is None

    def test_invalid_date(self):
        assert parse_date("2026-02-30") is None

    def test_partial_date(self):
        assert parse_date("2026-01") is None

    def test_datetime_string(self):
        assert parse_date("2026-01-15T10:00:00") is None

    def test_first_day_of_year(self):
        assert parse_date("2026-01-01") == "2026-01-01"

    def test_last_day_of_year(self):
        assert parse_date("2026-12-31") == "2026-12-31"


class TestGetToday:
    """Test get_today function."""

    def test_returns_date_string(self):
        result = get_today()
        assert isinstance(result, str)
        assert len(result) == 10

    def test_format_is_correct(self):
        result = get_today()
        parts = result.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day

    def test_matches_current_date(self):
        expected = datetime.now().strftime("%Y-%m-%d")
        assert get_today() == expected


class TestGetDaysAgo:
    """Test get_days_ago function."""

    def test_zero_days(self):
        result = get_days_ago(0)
        assert result == datetime.now().strftime("%Y-%m-%d")

    def test_one_day(self):
        result = get_days_ago(1)
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result == expected

    def test_thirty_days(self):
        result = get_days_ago(30)
        expected = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        assert result == expected

    def test_format_is_correct(self):
        result = get_days_ago(7)
        parts = result.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4


class TestGetDateRange:
    """Test get_date_range function."""

    def test_default_end_date(self):
        start, end = get_date_range(7)
        expected_end = datetime.now().strftime("%Y-%m-%d")
        assert end == expected_end
        # Start should be 7 days before end
        expected_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert start == expected_start

    def test_custom_end_date(self):
        start, end = get_date_range(7, end_date="2026-01-15")
        assert end == "2026-01-15"
        assert start == "2026-01-08"

    def test_zero_days(self):
        start, end = get_date_range(0, end_date="2026-01-15")
        assert start == "2026-01-15"
        assert end == "2026-01-15"

    def test_large_range(self):
        start, end = get_date_range(365, end_date="2026-12-31")
        assert end == "2026-12-31"
        assert start == "2025-12-31"  # 2026-12-31 minus 365 days

    def test_returns_tuple(self):
        result = get_date_range(7)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_start_before_end(self):
        start, end = get_date_range(30)
        assert start < end
