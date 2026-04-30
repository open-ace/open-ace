#!/usr/bin/env python3
"""
Unit tests for utils.py module.
"""

import pytest
from utils import format_tokens, parse_date, get_today, get_days_ago, aggregate_daily_stats


class TestFormatTokens:
    """Tests for format_tokens function."""

    def test_format_tokens_less_than_thousand(self):
        """Test formatting tokens less than 1000."""
        assert format_tokens(500) == "500"
        assert format_tokens(0) == "0"
        assert format_tokens(999) == "999"

    def test_format_tokens_thousands(self):
        """Test formatting tokens in thousands (K)."""
        assert format_tokens(1000) == "1.00K"
        assert format_tokens(5500) == "5.50K"
        assert format_tokens(999000) == "999.00K"

    def test_format_tokens_millions(self):
        """Test formatting tokens in millions (M)."""
        assert format_tokens(1_000_000) == "1.00M"
        assert format_tokens(5_500_000) == "5.50M"

    def test_format_tokens_billions(self):
        """Test formatting tokens in billions (B)."""
        assert format_tokens(1_000_000_000) == "1.00B"
        assert format_tokens(2_500_000_000) == "2.50B"


class TestParseDate:
    """Tests for parse_date function."""

    def test_valid_date(self):
        """Test parsing valid date strings."""
        assert parse_date("2026-03-09") == "2026-03-09"
        assert parse_date("2025-01-01") == "2025-01-01"

    def test_invalid_date_format(self):
        """Test parsing invalid date formats."""
        assert parse_date("2026/03/09") is None
        assert parse_date("09-03-2026") is None
        assert parse_date("invalid") is None

    def test_empty_date(self):
        """Test parsing empty or None date."""
        assert parse_date("") is None
        assert parse_date(None) is None

    def test_invalid_date_values(self):
        """Test parsing dates with invalid values."""
        assert parse_date("2026-13-01") is None  # Invalid month
        assert parse_date("2026-02-30") is None  # Invalid day


class TestDateHelpers:
    """Tests for date helper functions."""

    def test_get_today(self):
        """Test get_today returns valid date format."""
        from datetime import datetime

        today = get_today()
        expected = datetime.now().strftime("%Y-%m-%d")
        assert today == expected

    def test_get_days_ago(self):
        """Test get_days_ago returns correct date."""
        from datetime import datetime, timedelta

        result = get_days_ago(7)
        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert result == expected

        result = get_days_ago(0)
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result == expected


class TestAggregateDailyStats:
    """Tests for aggregate_daily_stats function."""

    def test_aggregate_empty(self):
        """Test aggregating empty list."""
        result = aggregate_daily_stats([])
        assert result["total_tokens"] == 0
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["models"] == []

    def test_aggregate_single_entry(self):
        """Test aggregating single entry."""
        entries = [
            {
                "tokens_used": 100,
                "input_tokens": 80,
                "output_tokens": 20,
                "cache_tokens": 10,
                "models_used": ["gpt-4"],
            }
        ]
        result = aggregate_daily_stats(entries)
        assert result["total_tokens"] == 100
        assert result["input_tokens"] == 80
        assert result["output_tokens"] == 20
        assert result["cache_tokens"] == 10
        assert result["models"] == ["gpt-4"]

    def test_aggregate_multiple_entries(self):
        """Test aggregating multiple entries."""
        entries = [
            {
                "tokens_used": 100,
                "input_tokens": 80,
                "output_tokens": 20,
                "cache_tokens": 10,
                "models_used": ["gpt-4"],
            },
            {
                "tokens_used": 200,
                "input_tokens": 150,
                "output_tokens": 50,
                "cache_tokens": 20,
                "models_used": ["claude-3"],
            },
            {
                "tokens_used": 50,
                "input_tokens": 40,
                "output_tokens": 10,
                "cache_tokens": 5,
                "models_used": ["gpt-4", "gpt-3.5"],
            },
        ]
        result = aggregate_daily_stats(entries)
        assert result["total_tokens"] == 350
        assert result["input_tokens"] == 270
        assert result["output_tokens"] == 80
        assert result["cache_tokens"] == 35
        # Models should be sorted
        assert sorted(result["models"]) == ["claude-3", "gpt-3.5", "gpt-4"]

    def test_aggregate_missing_fields(self):
        """Test aggregating entries with missing fields."""
        entries = [
            {"tokens_used": 100},
            {"tokens_used": 200, "input_tokens": 150},
            {},  # Empty entry
        ]
        result = aggregate_daily_stats(entries)
        assert result["total_tokens"] == 300
        assert result["input_tokens"] == 150
        assert result["output_tokens"] == 0
        assert result["models"] == []
