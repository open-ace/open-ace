"""Unit tests for datetime_utils module."""
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
        result = ensure_utc_suffix('2026-08-06T09:54:57.981635')
        assert result == '2026-08-06T09:54:57.981635Z'

    def test_with_z_suffix(self):
        """Test timestamp with Z suffix remains unchanged."""
        result = ensure_utc_suffix('2026-08-06T09:54:57.981635Z')
        assert result == '2026-08-06T09:54:57.981635Z'

    def test_with_timezone_offset(self):
        """Test timestamp with timezone offset remains unchanged."""
        result = ensure_utc_suffix('2026-08-06T09:54:57.981635+00:00')
        assert result == '2026-08-06T09:54:57.981635+00:00'

    def test_with_negative_timezone_offset(self):
        """Test timestamp with negative timezone offset remains unchanged."""
        result = ensure_utc_suffix('2026-08-06T09:54:57.981635-08:00')
        assert result == '2026-08-06T09:54:57.981635-08:00'

    def test_whitespace_string(self):
        """Test that whitespace-only string returns None."""
        assert ensure_utc_suffix("   ") is None