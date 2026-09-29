# tests/unit/test_e2e_date_range_expectations.py
"""Unit contract for the e2e shared date-range expectation helper.

`tests/e2e/sync_helpers.expected_default_date_range` is the single source
every date-prefill e2e asserts against. It must mirror the product contract
pinned by #3276: exactly N calendar days through today, inclusive on both
ends (start = today-(N-1)), computed in the local calendar — never
UTC/toISOString semantics. The helper is deliberately pure Python (no
frontend import), so these anchors verify it against hand-computed
calendars, including cross-month, cross-year and leap-February boundaries.
"""

import re
from datetime import datetime, timedelta

import pytest

from tests.e2e.sync_helpers import expected_default_date_range

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _d(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d")


@pytest.mark.parametrize(
    ("now", "days", "expected_start", "expected_end"),
    [
        # Plain 30-day anchor.
        (datetime(2026, 3, 31), 30, "2026-03-02", "2026-03-31"),
        # Cross-month: Mar 7 .. Apr 5 is exactly 30 days.
        (datetime(2026, 4, 5), 30, "2026-03-07", "2026-04-05"),
        # Cross-year: Dec 7 .. Jan 5 is exactly 30 days.
        (datetime(2026, 1, 5), 30, "2025-12-07", "2026-01-05"),
        # Leap February: Feb 1 .. Mar 1 is 30 days (29 + 1).
        (datetime(2024, 3, 1), 30, "2024-02-01", "2024-03-01"),
        # The 365-day All-button fallback: today-364 .. today (Apr 1 2025 ..
        # Mar 31 2026 is exactly 365 days across a non-leap Feb).
        (datetime(2026, 3, 31), 365, "2025-04-01", "2026-03-31"),
        # Single-day window collapses to start == end == today.
        (datetime(2026, 7, 15), 1, "2026-07-15", "2026-07-15"),
        (datetime(2026, 7, 15), 7, "2026-07-09", "2026-07-15"),
    ],
)
def test_fixed_date_anchors(now, days, expected_start, expected_end):
    start, end = expected_default_date_range(days, now=now)
    assert (start, end) == (expected_start, expected_end)


def test_default_is_30_days():
    start, end = expected_default_date_range()
    assert (_d(end) - _d(start)).days == 29


@pytest.mark.parametrize("days", [0, -5])
def test_non_positive_days_degenerate_to_today(days):
    # Mirrors the frontend guard in getDefaultDateRange: never an inverted
    # or future-dated window.
    start, end = expected_default_date_range(days, now=datetime(2026, 8, 17))
    assert (start, end) == ("2026-08-17", "2026-08-17")


@pytest.mark.parametrize("days", list(range(1, 401)))
def test_inclusive_window_property_and_format(days):
    start, end = expected_default_date_range(days, now=datetime(2026, 8, 17))
    assert ISO_DATE.match(start) and ISO_DATE.match(end)
    assert _d(end) - _d(start) == timedelta(days=days - 1)


def test_clock_default_end_is_today():
    start, end = expected_default_date_range(30)
    now = datetime.now()
    # The window is derived from the current local clock; the end must be
    # the call's own local date and the start exactly 29 calendar days back.
    assert _d(end).date() == now.date()
    assert _d(start) == _d(end) - timedelta(days=29)
