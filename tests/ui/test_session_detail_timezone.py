"""
Test: Session detail timezone display

Verifies that _format_dt correctly serializes datetimes so that the frontend
displays them in the user's local timezone (e.g., CST/UTC+8).

Key invariant: all naive datetimes in this codebase are UTC (created via
datetime.now(timezone.utc).replace(tzinfo=None)), so _format_dt must append
+00:00 to tell the frontend the correct reference timezone.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.workspace.session_manager import _format_dt

CST = timezone(timedelta(hours=8))


class TestFormatDt:
    """Unit tests for _format_dt timezone serialization."""

    def test_none_returns_none(self):
        assert _format_dt(None) is None

    def test_naive_datetime_gets_utc_suffix(self):
        """Naive datetimes (UTC stored without tzinfo) must get +00:00."""
        dt = datetime(2026, 5, 24, 15, 30, 0)
        result = _format_dt(dt)
        assert (
            result == "2026-05-24T15:30:00+00:00"
        ), f"Expected +00:00 suffix for naive datetime, got: {result}"

    def test_utc_aware_datetime_preserves_offset(self):
        """Timezone-aware UTC datetimes preserve their +00:00 offset."""
        dt = datetime(2026, 5, 24, 15, 30, 0, tzinfo=timezone.utc)
        result = _format_dt(dt)
        assert "+00:00" in result
        assert result == "2026-05-24T15:30:00+00:00"

    def test_cst_aware_datetime_preserves_offset(self):
        """Timezone-aware CST datetimes preserve their +08:00 offset."""
        dt = datetime(2026, 5, 24, 23, 30, 0, tzinfo=CST)
        result = _format_dt(dt)
        assert "+08:00" in result

    def test_frontend_converts_utc_to_cst_correctly(self):
        """Simulate frontend: parse UTC string, convert to CST."""
        # Server stores 15:30 UTC as naive datetime
        utc_naive = datetime(2026, 5, 24, 15, 30, 0)
        serialized = _format_dt(utc_naive)
        assert serialized == "2026-05-24T15:30:00+00:00"

        # Frontend: new Date("2026-05-24T15:30:00+00:00") in CST → 23:30
        parsed = datetime.fromisoformat(serialized)
        cst_time = parsed.astimezone(CST)
        assert cst_time.hour == 23
        assert cst_time.minute == 30

    def test_no_double_conversion_for_utc_values(self):
        """Ensure UTC 15:30 is NOT displayed as 15:30 local time (the bug)."""
        utc_naive = datetime(2026, 5, 24, 15, 30, 0)
        serialized = _format_dt(utc_naive)
        # If +00:00 were missing, frontend would treat 15:30 as local (wrong).
        assert (
            "+00:00" in serialized
        ), "Missing +00:00 would cause frontend to interpret UTC as local time"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
