"""Date Limits Configuration - configurable limits for date range validation.

Provides runtime configuration for date range validation limits with request
context caching (similar to feature_flags.py).

Issue #2738: Centralized configuration for date range validation.
"""

from __future__ import annotations

import os

from flask import g

# Environment variable names
ENV_MAX_DATE_RANGE_DAYS = "OPENACE_MAX_DATE_RANGE_DAYS"
ENV_MAX_MONTHS = "OPENACE_MAX_MONTHS"
ENV_MAX_DAYS = "OPENACE_MAX_DAYS"
ENV_ALLOW_FUTURE_DATE = "OPENACE_ALLOW_FUTURE_DATE"

# Default values
DEFAULT_MAX_DATE_RANGE_DAYS = 365
DEFAULT_MAX_MONTHS = 24
DEFAULT_MAX_DAYS = 365
DEFAULT_ALLOW_FUTURE_DATE = False


def get_max_date_range_days() -> int:
    """Get maximum date range days from environment with request context caching.

    Returns:
        Maximum number of days allowed in a date range query.

    Note:
        Outside of request context, returns the default or environment value
        without caching (useful for unit tests).
    """
    try:
        from flask import g

        if hasattr(g, "_max_date_range_days"):
            return g._max_date_range_days  # type: ignore[no-any-return]
        value = int(os.environ.get(ENV_MAX_DATE_RANGE_DAYS, DEFAULT_MAX_DATE_RANGE_DAYS))
        g._max_date_range_days = value  # type: ignore[attr-defined]
        return value
    except RuntimeError:
        # Outside application context (e.g., unit tests)
        return int(os.environ.get(ENV_MAX_DATE_RANGE_DAYS, DEFAULT_MAX_DATE_RANGE_DAYS))


def get_max_months() -> int:
    """Get maximum months for trend queries with request context caching.

    Returns:
        Maximum number of months allowed in trend queries.
    """
    try:
        from flask import g

        if hasattr(g, "_max_months"):
            return g._max_months  # type: ignore[no-any-return]
        value = int(os.environ.get(ENV_MAX_MONTHS, DEFAULT_MAX_MONTHS))
        g._max_months = value  # type: ignore[attr-defined]
        return value
    except RuntimeError:
        # Outside application context (e.g., unit tests)
        return int(os.environ.get(ENV_MAX_MONTHS, DEFAULT_MAX_MONTHS))


def get_max_days() -> int:
    """Get maximum days for optimization queries with request context caching.

    Returns:
        Maximum number of days allowed in optimization queries.
    """
    try:
        from flask import g

        if hasattr(g, "_max_days"):
            return g._max_days  # type: ignore[no-any-return]
        value = int(os.environ.get(ENV_MAX_DAYS, DEFAULT_MAX_DAYS))
        g._max_days = value  # type: ignore[attr-defined]
        return value
    except RuntimeError:
        # Outside application context (e.g., unit tests)
        return int(os.environ.get(ENV_MAX_DAYS, DEFAULT_MAX_DAYS))


def is_future_date_allowed() -> bool:
    """Check if future dates are allowed with request context caching.

    Returns:
        True if future dates are allowed, False otherwise.
    """
    try:
        from flask import g

        if hasattr(g, "_allow_future_date"):
            return g._allow_future_date  # type: ignore[no-any-return]
        value = (
            os.environ.get(ENV_ALLOW_FUTURE_DATE, str(DEFAULT_ALLOW_FUTURE_DATE)).lower() == "true"
        )
        g._allow_future_date = value  # type: ignore[attr-defined]
        return value
    except RuntimeError:
        # Outside application context (e.g., unit tests)
        return (
            os.environ.get(ENV_ALLOW_FUTURE_DATE, str(DEFAULT_ALLOW_FUTURE_DATE)).lower() == "true"
        )


def set_date_limits_for_testing(
    max_days: int | None = None,
    max_months: int | None = None,
    max_days_window: int | None = None,
    allow_future: bool | None = None,
) -> None:
    """Set temporary configuration values for testing.

    Usage:
        In unit tests, call this function directly. pytest fixture
        (conftest.py) will automatically clean up after each test.

    Example:
        def test_custom_max_days():
            set_date_limits_for_testing(max_days=90)
            # Test code...
            # fixture cleans up automatically after test
    """
    if max_days is not None:
        g._max_date_range_days = max_days  # type: ignore[attr-defined]
    if max_months is not None:
        g._max_months = max_months  # type: ignore[attr-defined]
    if max_days_window is not None:
        g._max_days = max_days_window  # type: ignore[attr-defined]
    if allow_future is not None:
        g._allow_future_date = allow_future  # type: ignore[attr-defined]


__all__ = [
    "get_max_date_range_days",
    "get_max_months",
    "get_max_days",
    "is_future_date_allowed",
    "set_date_limits_for_testing",
    "ENV_MAX_DATE_RANGE_DAYS",
    "ENV_MAX_MONTHS",
    "ENV_MAX_DAYS",
    "ENV_ALLOW_FUTURE_DATE",
    "DEFAULT_MAX_DATE_RANGE_DAYS",
    "DEFAULT_MAX_MONTHS",
    "DEFAULT_MAX_DAYS",
    "DEFAULT_ALLOW_FUTURE_DATE",
]
