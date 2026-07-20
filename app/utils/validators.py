"""
Open ACE - Validators

Input validation functions.
"""

from __future__ import annotations
import re


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
