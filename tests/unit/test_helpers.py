"""Unit tests for helper utilities."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.utils.helpers import format_tokens, get_date_range, get_days_ago, get_today, parse_date


class TestFormatTokens:
    """Test format_tokens function."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0"),
            (42, "42"),
            (500, "500"),
            (1500, "1.50K"),
            (1000, "1.00K"),
            (1_500_000, "1.50M"),
            (1_000_000, "1.00M"),
            (1_500_000_000, "1.50B"),
            (1_000_000_000, "1.00B"),
            (999_999_999, "1000.00M"),
            (999, "999"),
            (999_999, "1000.00K"),
        ],
        ids=[
            "zero",
            "small_number",
            "hundreds",
            "thousands",
            "exact_thousand",
            "millions",
            "exact_million",
            "billions",
            "exact_billion",
            "large_below_billion",
            "boundary_thousand",
            "boundary_million",
        ],
    )
    def test_format_tokens(self, value, expected):
        assert format_tokens(value) == expected


class TestParseDate:
    """Test parse_date function."""

    @pytest.mark.parametrize(
        "date_str,expected",
        [
            ("2026-01-15", "2026-01-15"),
            (None, None),
            ("", None),
            ("01-15-2026", None),
            ("2026-02-30", None),
            ("2026-01", None),
            ("2026-01-15T10:00:00", None),
            ("2026-01-01", "2026-01-01"),
            ("2026-12-31", "2026-12-31"),
        ],
        ids=[
            "valid_date",
            "none_input",
            "empty_string",
            "invalid_format",
            "invalid_date",
            "partial_date",
            "datetime_string",
            "first_day_of_year",
            "last_day_of_year",
        ],
    )
    def test_parse_date(self, date_str, expected):
        assert parse_date(date_str) == expected


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

    @pytest.mark.parametrize(
        "days",
        [0, 1, 30],
        ids=["zero_days", "one_day", "thirty_days"],
    )
    def test_get_days_ago_returns_correct_date(self, days):
        result = get_days_ago(days)
        expected = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
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

    @pytest.mark.parametrize(
        "days,end_date,expected_start,expected_end",
        [
            (7, "2026-01-15", "2026-01-08", "2026-01-15"),
            (0, "2026-01-15", "2026-01-15", "2026-01-15"),
            (365, "2026-12-31", "2025-12-31", "2026-12-31"),
        ],
        ids=["custom_end_date", "zero_days", "large_range"],
    )
    def test_get_date_range(self, days, end_date, expected_start, expected_end):
        start, end = get_date_range(days, end_date=end_date)
        assert end == expected_end
        assert start == expected_start

    def test_returns_tuple(self):
        result = get_date_range(7)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_start_before_end(self):
        start, end = get_date_range(30)
        assert start < end
