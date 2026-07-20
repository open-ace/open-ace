"""
Open ACE - Helper Utilities

Common helper functions for the application.
"""

from __future__ import annotations


import re
from datetime import datetime, timedelta


def format_tokens(tokens: int) -> str:
    """
    Format token count with human-readable units (K, M, B).

    Args:
        tokens: Token count.

    Returns:
        str: Formatted token string.
    """
    if tokens >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:.2f}B"
    elif tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.2f}K"
    else:
        return str(tokens)


def parse_date(date_str: str) -> str | None:
    """
    Validate and normalize a date string (YYYY-MM-DD).

    Args:
        date_str: Date string to validate.

    Returns:
        Optional[str]: Validated date string or None.
    """
    if not date_str:
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None


def get_today() -> str:
    """
    Get today's date in YYYY-MM-DD format.

    Returns:
        str: Today's date.
    """
    return datetime.now().strftime("%Y-%m-%d")


def get_days_ago(days: int) -> str:
    """
    Get the date that was 'days' days ago.

    Args:
        days: Number of days ago.

    Returns:
        str: Date string.
    """
    date = datetime.now() - timedelta(days=days)
    return date.strftime("%Y-%m-%d")


def get_date_range(days: int, end_date: str | None = None) -> tuple:
    """
    Get a date range for the past N days.

    Args:
        days: Number of days.
        end_date: Optional end date (defaults to today).

    Returns:
        tuple: (start_date, end_date)
    """
    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d")
    else:
        end = datetime.now()

    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def parse_db_datetime(value) -> datetime | None:
    """Parse a datetime value coming back from the database.

    Handles ``datetime`` instances, ISO 8601 strings (``YYYY-MM-DDTHH:MM:SS``),
    and PostgreSQL's default textual timestamp format. PostgreSQL emits a space
    as the date/time separator (``YYYY-MM-DD HH:MM:SS``) and a variable number
    of fractional-second digits, neither of which ``datetime.fromisoformat``
    accepts on Python < 3.11. This helper normalises both so parsing is robust
    across interpreter versions.

    Args:
        value: A ``datetime``, a string, or ``None``.

    Returns:
        Optional[datetime]: The parsed datetime, or ``None`` for null/unknown
        inputs.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        if len(normalized) >= 10 and normalized[10] == " ":
            normalized = normalized[:10] + "T" + normalized[11:]
        # Pad variable-length fractional seconds to a fixed 6 digits so that
        # ``datetime.fromisoformat`` (Python < 3.11) can parse them.
        normalized = re.sub(
            r"\.(\d+)",
            lambda m: "." + m.group(1).ljust(6, "0"),
            normalized,
        )
        return datetime.fromisoformat(normalized)
    return None
