"""Datetime utilities for timestamp handling."""

import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

# Pre-compile regex pattern for better performance in batch operations
_TZ_PATTERN = re.compile(r"[+-]\d{2}:\d{2}$")


class ForecastWindow(NamedTuple):
    """Forecast window parameters for Issue #3244.

    Attributes:
        start_date: Start date of the window (inclusive, YYYY-MM-DD string).
        end_date: End date of the window (inclusive, YYYY-MM-DD string).
        days: Number of days in the window.
    """

    start_date: str
    end_date: str
    days: int


def get_business_date() -> str:
    """Get current business date in UTC.

    Returns:
        Current UTC date as YYYY-MM-DD string.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_forecast_window(
    business_date: str,
    days: int = 7,
    first_activity_date: str | None = None,
) -> ForecastWindow:
    """Calculate forecast window boundaries.

    The window excludes the current incomplete day (business_date) and covers
    the previous `days` completed calendar days.

    Args:
        business_date: Current business date in UTC (YYYY-MM-DD).
        days: Number of days for the window (default 7).
        first_activity_date: Optional first activity date to bound the window start.
            The window start will not be earlier than this date.

    Returns:
        ForecastWindow with start_date, end_date, and actual days count.

    Examples:
        >>> get_forecast_window("2026-08-31", days=7)
        ForecastWindow(start_date='2026-08-24', end_date='2026-08-30', days=7)
        >>> get_forecast_window("2026-08-31", days=7, first_activity_date="2026-08-28")
        ForecastWindow(start_date='2026-08-28', end_date='2026-08-30', days=3)
    """
    # Parse business date
    business_dt = datetime.strptime(business_date, "%Y-%m-%d")

    # End date is the day before business date (exclude incomplete current day)
    end_date_dt = business_dt - timedelta(days=1)

    # Start date is `days` before end date
    start_date_dt = end_date_dt - timedelta(days=days - 1)

    # Apply first activity date boundary if provided
    if first_activity_date:
        first_activity_dt = datetime.strptime(first_activity_date, "%Y-%m-%d")
        if first_activity_dt > start_date_dt:
            start_date_dt = first_activity_dt

    # Calculate actual days in window
    actual_days = (end_date_dt - start_date_dt).days + 1

    return ForecastWindow(
        start_date=start_date_dt.strftime("%Y-%m-%d"),
        end_date=end_date_dt.strftime("%Y-%m-%d"),
        days=actual_days,
    )


def generate_date_spine(start_date: str, end_date: str) -> list[str]:
    """Generate a continuous sequence of dates between start and end (inclusive).

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        List of date strings from start to end inclusive.

    Examples:
        >>> generate_date_spine("2026-08-01", "2026-08-03")
        ['2026-08-01', '2026-08-02', '2026-08-03']
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    days = (end_dt - start_dt).days + 1
    return [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def ensure_utc_suffix(timestamp: str | datetime | None) -> str | None:
    """Ensure timestamp has UTC 'Z' suffix for frontend parsing.

    Handles ``datetime`` instances from PostgreSQL (psycopg2/psycopg automatically
    converts TIMESTAMP WITHOUT TIME ZONE to Python datetime objects) and ISO format
    strings from SQLite.

    PostgreSQL TIMESTAMP WITHOUT TIME ZONE stores timestamps without timezone info.
    JavaScript needs timezone info to correctly parse UTC timestamps.
    This function adds 'Z' suffix when returning timestamps to frontend.

    Note:
        All datetime inputs are assumed to be in UTC. Naive datetime objects
        (without timezone info) are treated as UTC timestamps.

    Warning:
        This function does not validate timezone correctness. Callers must
        ensure that naive datetime objects represent UTC timestamps.

    Args:
        timestamp: A ``datetime`` object, ISO format string, or ``None``.

    Returns:
        ISO format timestamp with 'Z' suffix or timezone offset, or ``None``
        for null/empty inputs.

    Examples:
        >>> from datetime import datetime, timezone
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635')
        '2026-08-06T09:54:57.981635Z'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635Z')
        '2026-08-06T09:54:57.981635Z'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635+00:00')
        '2026-08-06T09:54:57.981635+00:00'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635-08:00')
        '2026-08-06T09:54:57.981635-08:00'
        >>> ensure_utc_suffix(datetime(2026, 8, 6, 9, 54, 57))
        '2026-08-06T09:54:57Z'
        >>> ensure_utc_suffix(datetime(2026, 8, 6, 9, 54, 57, tzinfo=timezone.utc))
        '2026-08-06T09:54:57+00:00'
        >>> ensure_utc_suffix(None)
        None
        >>> ensure_utc_suffix('')
        None
    """
    # Handle datetime objects from PostgreSQL
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    if not timestamp or not timestamp.strip():
        return None
    # Check if timestamp already has timezone info
    # Z suffix or timezone offset like +00:00 or -08:00 at the end
    if timestamp.endswith("Z") or _TZ_PATTERN.search(timestamp):
        return timestamp
    return timestamp + "Z"


def parse_utc(value: str | datetime | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (accepting a trailing ``Z``) into a datetime.

    Read-path companion to :func:`ensure_utc_suffix`. Python 3.10's
    ``datetime.fromisoformat`` rejects the trailing ``Z`` that
    ``ensure_utc_suffix`` emits (Python 3.11+ accepts it), so serializing a
    timestamp with ``ensure_utc_suffix`` and reading it back with a bare
    ``fromisoformat`` breaks the round-trip on 3.10. Normalizing ``Z`` to
    ``+00:00`` keeps parsing consistent across supported Python versions.

    This is the tight companion to :func:`ensure_utc_suffix` for model
    (de)serialization, where inputs are ISO strings from ``datetime.isoformat()``.
    For raw database text (space-separated timestamps, variable fractional-second
    widths) use :func:`app.utils.helpers.parse_db_datetime` instead.

    Args:
        value: A ``datetime`` (returned unchanged), an ISO-8601 string, or
            ``None``.

    Returns:
        A ``datetime`` (timezone-aware when the string carried ``Z`` or an
        offset), or ``None`` for ``None``/blank input.

    Examples:
        >>> parse_utc(None) is None
        True
        >>> parse_utc('2026-08-06T09:54:57Z').tzinfo is not None
        True
        >>> parse_utc('2026-08-06T09:54:57').tzinfo is None
        True
    """
    if value is None or isinstance(value, datetime):
        return value
    if not value.strip():
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
