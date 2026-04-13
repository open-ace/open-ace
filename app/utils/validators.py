#!/usr/bin/env python3
"""
Open ACE - Validators

Input validation functions.
"""

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


def validate_password(password: str) -> tuple:
    """
    Validate a password.

    Args:
        password: Password to validate.

    Returns:
        tuple: (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if len(password) > 128:
        return False, "Password must be less than 128 characters"
    return True, None
