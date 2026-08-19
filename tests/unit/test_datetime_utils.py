"""Unit tests for datetime_utils module."""

from datetime import datetime, timedelta, timezone

import pytest

from app.utils.datetime_utils import ensure_utc_suffix, parse_utc


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


class TestParseUtc:
    """Test cases for parse_utc (read-path companion to ensure_utc_suffix)."""

    def test_none_returns_none(self):
        """None input returns None."""
        assert parse_utc(None) is None

    def test_blank_returns_none(self):
        """Blank/whitespace input returns None."""
        assert parse_utc("   ") is None

    def test_datetime_passthrough(self):
        """A datetime is returned unchanged (defensive against pre-parsed input)."""
        dt = datetime(2025, 12, 1, 8, 30, 0)
        assert parse_utc(dt) is dt

    def test_z_suffix_parses_to_aware_utc(self):
        """Regression: Python 3.10's fromisoformat rejects the 'Z' that
        ensure_utc_suffix emits; parse_utc must accept it on every version."""
        assert parse_utc("2025-12-01T08:30:00Z") == datetime(
            2025, 12, 1, 8, 30, 0, tzinfo=timezone.utc
        )

    def test_z_suffix_with_microseconds(self):
        """The exact shape isoformat() emits for non-zero microseconds -- the
        form that broke main's 3.10 lane."""
        assert parse_utc("2026-08-06T09:54:57.981635Z") == datetime(
            2026, 8, 6, 9, 54, 57, 981635, tzinfo=timezone.utc
        )

    def test_naive_string_parses_naive(self):
        """A string with no offset parses to a naive datetime."""
        result = parse_utc("2025-12-01T08:30:00")
        assert result == datetime(2025, 12, 1, 8, 30, 0)
        assert result.tzinfo is None

    def test_offset_string_preserved(self):
        """An explicit offset is preserved (not coerced to UTC)."""
        assert parse_utc("2025-12-01T08:30:00-08:00") == datetime(
            2025, 12, 1, 8, 30, 0, tzinfo=timezone(timedelta(hours=-8))
        )

    def test_roundtrip_with_ensure_utc_suffix(self):
        """The exact serialize -> deserialize path the models use."""
        serialized = ensure_utc_suffix(datetime(2025, 12, 1, 8, 30, 0))
        assert serialized == "2025-12-01T08:30:00Z"
        assert parse_utc(serialized) == datetime(2025, 12, 1, 8, 30, 0, tzinfo=timezone.utc)
