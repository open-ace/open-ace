"""Datetime utilities for timestamp handling."""

import re
from datetime import datetime

# Pre-compile regex pattern for better performance in batch operations
_TZ_PATTERN = re.compile(r"[+-]\d{2}:\d{2}$")


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
