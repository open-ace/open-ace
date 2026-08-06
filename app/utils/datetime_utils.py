"""Datetime utilities for timestamp handling."""
import re


def ensure_utc_suffix(timestamp: str | None) -> str | None:
    """Ensure timestamp has UTC 'Z' suffix for frontend parsing.

    PostgreSQL TIMESTAMP WITHOUT TIME ZONE stores timestamps without timezone info.
    JavaScript needs timezone info to correctly parse UTC timestamps.
    This function adds 'Z' suffix when returning timestamps to frontend.

    Args:
        timestamp: ISO format timestamp string (may or may not have timezone info)

    Returns:
        Timestamp with 'Z' suffix, or None if input is None or empty

    Examples:
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635')
        '2026-08-06T09:54:57.981635Z'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635Z')
        '2026-08-06T09:54:57.981635Z'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635+00:00')
        '2026-08-06T09:54:57.981635+00:00'
        >>> ensure_utc_suffix('2026-08-06T09:54:57.981635-08:00')
        '2026-08-06T09:54:57.981635-08:00'
        >>> ensure_utc_suffix(None)
        None
        >>> ensure_utc_suffix('')
        None
    """
    if not timestamp or not timestamp.strip():
        return None
    # Check if timestamp already has timezone info
    # Z suffix or timezone offset like +00:00 or -08:00 at the end
    if timestamp.endswith('Z') or re.search(r'[+-]\d{2}:\d{2}$', timestamp):
        return timestamp
    return timestamp + 'Z'
