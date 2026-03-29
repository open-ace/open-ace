#!/usr/bin/env python3
"""
Open ACE - Helper Utilities

Common helper functions for the application.
"""

from datetime import datetime, timedelta
from typing import Optional


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


def parse_date(date_str: str) -> Optional[str]:
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


def get_date_range(days: int, end_date: Optional[str] = None) -> tuple:
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
