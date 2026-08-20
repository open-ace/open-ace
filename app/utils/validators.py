"""
Open ACE - Validators

Input validation functions.

Issue #2738: Added date range validation functions.
"""

import re
from datetime import date, datetime, timezone
from typing import Literal


def validate_date(date_str: str) -> bool:
    """
    Validate a date string in YYYY-MM-DD format.

    Args:
        date_str: Date string to validate.

    Returns:
        bool: True if valid.
    """
    if not date_str:
        return False
    pattern = r"^\d{4}-\d{2}-\d{2}$"
    if not re.match(pattern, date_str):
        return False
    try:
        from datetime import datetime

        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_tool_name(tool_name: str) -> bool:
    """
    Validate a tool name.

    Args:
        tool_name: Tool name to validate.

    Returns:
        bool: True if valid.
    """
    if not tool_name:
        return False
    # Allow alphanumeric, underscore, hyphen
    pattern = r"^[a-zA-Z0-9_-]+$"
    return bool(re.match(pattern, tool_name))


def validate_host_name(host_name: str) -> bool:
    """
    Validate a host name.

    Args:
        host_name: Host name to validate.

    Returns:
        bool: True if valid.
    """
    if not host_name:
        return False
    # Allow alphanumeric, underscore, hyphen, dot
    pattern = r"^[a-zA-Z0-9_.-]+$"
    return bool(re.match(pattern, host_name))


def validate_username(username: str) -> bool:
    """
    Validate a username.

    Args:
        username: Username to validate.

    Returns:
        bool: True if valid.
    """
    if not username:
        return False
    if len(username) < 2 or len(username) > 50:
        return False
    # Allow alphanumeric, underscore, hyphen, and Chinese characters (CJK)
    # Chinese: \u4e00-\u9fff (CJK Unified Ideographs)
    # Extended: \u3400-\u4dbf (CJK Unified Ideographs Extension A)
    pattern = r"^[a-zA-Z0-9_\-\u4e00-\u9fff\u3400-\u4dbf]+$"
    return bool(re.match(pattern, username))


def validate_email(email: str) -> bool:
    """
    Validate an email address.

    Args:
        email: Email to validate.

    Returns:
        bool: True if valid.
    """
    if not email:
        return False
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_password(password: str, policy_settings: dict | None = None) -> tuple:
    """
    Validate a password against security policy.

    Args:
        password: Password to validate.
        policy_settings: Optional dict from security_settings for policy validation.
                         If None, only a basic length check (default minimum 8)
                         is performed.

    Returns:
        tuple: (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"

    if len(password) > 128:
        return False, "Password must be less than 128 characters"

    # Minimum length is solely policy-driven so an admin-configured minimum
    # fully governs the rule — there is no hardcoded floor that could contradict
    # it. Defaults to 8 when no policy is supplied, preserving prior behavior.
    try:
        min_length = int((policy_settings or {}).get("password_min_length", 8))
    except (TypeError, ValueError):
        # Guard against malformed policy values (None / non-numeric) that
        # would otherwise surface as a 500 from this helper.
        min_length = 8
    # Floor at 1 so an admin misconfiguration (0 / negative) cannot silently
    # disable the minimum-length requirement entirely.
    min_length = max(min_length, 1)
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    # Complexity checks only apply when a policy is supplied.
    if policy_settings:
        if policy_settings.get("password_require_uppercase") and not re.search(r"[A-Z]", password):
            return False, "Password must contain uppercase letters"
        if policy_settings.get("password_require_lowercase") and not re.search(r"[a-z]", password):
            return False, "Password must contain lowercase letters"
        if policy_settings.get("password_require_number") and not re.search(r"[0-9]", password):
            return False, "Password must contain numbers"
        # Any non-word, non-space character counts as "special". This accepts
        # the common punctuation set (- + = ; ' / \ etc.) the previous narrow
        # class missed, so compliant passwords containing those are no longer
        # wrongly rejected. Underscore is a word char and intentionally excluded.
        if policy_settings.get("password_require_special") and not re.search(r"[^\w\s]", password):
            return False, "Password must contain special characters"

    return True, None


# ==================== Date Range Validation (Issue #2738) ====================


def validate_date_range(
    start_date: str | None,
    end_date: str | None,
    *,
    max_days: int | None = None,
    allow_future: bool | None = None,
) -> tuple[bool, str | None, date | None, date | None]:
    """Validate a date range for API queries.

    Validation order (highest priority first):
        1. Pair check: both dates must be provided together or neither
        2. Format check: must be valid YYYY-MM-DD
        3. Order check: start_date <= end_date
        4. Span check: range must not exceed max_days
        5. Future check: dates must not be in future (unless allowed)

    Args:
        start_date: Start date string or None
        end_date: End date string or None
        max_days: Maximum days allowed in range (None = use config default)
        allow_future: Whether future dates are allowed (None = use config default)

    Returns:
        tuple: (is_valid, error_code, parsed_start, parsed_end)
        - On success: (True, None, parsed_start_date, parsed_end_date)
        - When both None: (True, None, None, None) - caller applies default
        - On failure: (False, error_code, None, None)
    """
    # Import here to avoid circular imports
    from app.config.date_limits import get_max_date_range_days, is_future_date_allowed
    from app.utils.date_range_errors import (
        ERROR_DATE_RANGE_EXCEEDED,
        ERROR_FUTURE_DATE_NOT_ALLOWED,
        ERROR_INCOMPLETE_DATE_RANGE,
        ERROR_INVALID_DATE_FORMAT,
        ERROR_INVALID_DATE_ORDER,
    )

    # Priority 1: Pair check
    if start_date is None and end_date is None:
        # Both missing - caller applies default, no validation needed
        return True, None, None, None

    if start_date is None or end_date is None:
        # Only one provided - incomplete range
        return False, ERROR_INCOMPLETE_DATE_RANGE, None, None

    # Priority 2: Format check
    if not validate_date(start_date):
        return False, ERROR_INVALID_DATE_FORMAT, None, None

    if not validate_date(end_date):
        return False, ERROR_INVALID_DATE_FORMAT, None, None

    # Parse dates
    try:
        parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return False, ERROR_INVALID_DATE_FORMAT, None, None

    # Priority 3: Order check
    if parsed_start > parsed_end:
        return False, ERROR_INVALID_DATE_ORDER, None, None

    # Priority 4: Span check
    actual_max_days = max_days if max_days is not None else get_max_date_range_days()
    span_days = (parsed_end - parsed_start).days
    if span_days > actual_max_days:
        return False, ERROR_DATE_RANGE_EXCEEDED, None, None

    # Priority 5: Future date check
    allow_future_resolved = (
        allow_future if allow_future is not None else is_future_date_allowed()
    )
    if not allow_future_resolved:
        # Use UTC time for consistency with existing code (roi.py:161)
        now_utc = datetime.now(timezone.utc).date()
        if parsed_start > now_utc or parsed_end > now_utc:
            return False, ERROR_FUTURE_DATE_NOT_ALLOWED, None, None

    return True, None, parsed_start, parsed_end


def validate_time_window(
    value: int,
    param_name: str,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
) -> tuple[bool, str | None, int | None]:
    """Validate a time window parameter (months/days).

    Args:
        value: The integer value to validate
        param_name: Parameter name for error messages (e.g., "months", "days")
        min_val: Minimum allowed value (None = use default 1)
        max_val: Maximum allowed value (None = use config default)

    Returns:
        tuple: (is_valid, error_code, parsed_value)
        - On success: (True, None, value)
        - On failure: (False, error_code, None)
    """
    # Import here to avoid circular imports
    from app.config.date_limits import get_max_days, get_max_months
    from app.utils.date_range_errors import ERROR_INVALID_TIME_WINDOW

    # Determine bounds
    if min_val is None:
        min_val = 1

    if max_val is None:
        # Use config defaults based on param_name
        if param_name == "months":
            max_val = get_max_months()
        else:
            max_val = get_max_days()

    # Validate range
    if value < min_val or value > max_val:
        return False, ERROR_INVALID_TIME_WINDOW, None

    return True, None, value
