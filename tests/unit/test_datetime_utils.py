"""Unit tests for datetime_utils module."""

from datetime import datetime, timedelta, timezone

import pytest

from app.utils.datetime_utils import ensure_utc_suffix


class TestEnsureUtcSuffix:
    """Test cases for ensure_utc_suffix function."""

    def test_none_input(self):
        """Test that None input returns None."""
        assert ensure_utc_suffix(None) is None

    def test_empty_string(self):
        """Test that empty string returns None."""
        assert ensure_utc_suffix("") is None

    def test_without_suffix(self):
        """Test timestamp without Z suffix gets Z suffix added."""
        result = ensure_utc_suffix("2026-08-06T09:54:57.981635")
        assert result == "2026-08-06T09:54:57.981635Z"

    def test_with_z_suffix(self):
        """Test timestamp with Z suffix remains unchanged."""
        result = ensure_utc_suffix("2026-08-06T09:54:57.981635Z")
        assert result == "2026-08-06T09:54:57.981635Z"

    def test_with_timezone_offset(self):
        """Test timestamp with timezone offset remains unchanged."""
        result = ensure_utc_suffix("2026-08-06T09:54:57.981635+00:00")
        assert result == "2026-08-06T09:54:57.981635+00:00"

    def test_with_negative_timezone_offset(self):
        """Test timestamp with negative timezone offset remains unchanged."""
        result = ensure_utc_suffix("2026-08-06T09:54:57.981635-08:00")
        assert result == "2026-08-06T09:54:57.981635-08:00"

    def test_whitespace_string(self):
        """Test that whitespace-only string returns None."""
        assert ensure_utc_suffix("   ") is None

    # ========== datetime object tests ==========

    def test_datetime_naive_without_microseconds(self):
        """Test that naive datetime without microseconds gets Z suffix added."""
        dt = datetime(2026, 8, 6, 9, 54, 57)
        result = ensure_utc_suffix(dt)
        assert result == "2026-08-06T09:54:57Z"

    def test_datetime_naive_with_microseconds(self):
        """Test that naive datetime with microseconds gets Z suffix added."""
        dt = datetime(2026, 8, 6, 9, 54, 57, 981635)
        result = ensure_utc_suffix(dt)
        assert result == "2026-08-06T09:54:57.981635Z"

    def test_datetime_with_utc_timezone(self):
        """Test that datetime with UTC timezone preserves +00:00, no Z added."""
        dt = datetime(2026, 8, 6, 9, 54, 57, tzinfo=timezone.utc)
        result = ensure_utc_suffix(dt)
        # isoformat() outputs "2026-08-06T09:54:57+00:00", which should not get Z
        assert result == "2026-08-06T09:54:57+00:00"

    def test_datetime_with_non_utc_timezone(self):
        """Test that datetime with non-UTC timezone preserves offset."""
        tz = timezone(timedelta(hours=-8))
        dt = datetime(2026, 8, 6, 9, 54, 57, tzinfo=tz)
        result = ensure_utc_suffix(dt)
        # isoformat() outputs "2026-08-06T09:54:57-08:00", which should not get Z
        assert result == "2026-08-06T09:54:57-08:00"

    def test_datetime_none_returns_none(self):
        """Test that None datetime input returns None (boundary condition)."""
        # This is already covered by test_none_input, but we add it for completeness
        # to show that the function handles both str | datetime | None types
        assert ensure_utc_suffix(None) is None
