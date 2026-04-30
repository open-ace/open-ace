#!/usr/bin/env python3
"""
Open ACE - Formatters

Data formatting functions.
"""

from datetime import datetime
from typing import Optional


def format_usage_data(usage: dict) -> dict:
    """
    Format usage data for API response.

    Args:
        usage: Raw usage data.

    Returns:
        Dict: Formatted usage data.
    """
    return {
        "date": usage.get("date"),
        "tool_name": usage.get("tool_name"),
        "host_name": usage.get("host_name"),
        "tokens_used": usage.get("tokens_used", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_tokens": usage.get("cache_tokens", 0),
        "request_count": usage.get("request_count", 0),
        "models_used": usage.get("models_used"),
    }


def format_message_data(message: dict) -> dict:
    """
    Format message data for API response.

    Args:
        message: Raw message data.

    Returns:
        Dict: Formatted message data.
    """
    return {
        "id": message.get("id"),
        "date": message.get("date"),
        "tool_name": message.get("tool_name"),
        "host_name": message.get("host_name"),
        "message_id": message.get("message_id"),
        "role": message.get("role"),
        "content": message.get("content"),
        "tokens_used": message.get("tokens_used", 0),
        "model": message.get("model"),
        "timestamp": message.get("timestamp"),
        "sender_name": message.get("sender_name"),
    }


def format_user_data(user: dict, include_sensitive: bool = False) -> dict:
    """
    Format user data for API response.

    Args:
        user: Raw user data.
        include_sensitive: Whether to include sensitive fields.

    Returns:
        Dict: Formatted user data.
    """
    data = {
        "id": user.get("id"),
        "username": user.get("username"),
        "email": user.get("email"),
        "role": user.get("role"),
        "is_active": user.get("is_active", True),
        "created_at": user.get("created_at"),
        "last_login": user.get("last_login"),
    }

    if include_sensitive:
        data["daily_token_quota"] = user.get("daily_token_quota")
        data["monthly_token_quota"] = user.get("monthly_token_quota")

    return data


def format_timestamp(ts: Optional[str]) -> Optional[str]:
    """
    Format a timestamp string.

    Args:
        ts: Timestamp string.

    Returns:
        Optional[str]: Formatted timestamp or None.
    """
    if not ts:
        return None
    try:
        # Try parsing ISO format
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts


def format_number(num: int) -> str:
    """
    Format a number with thousand separators.

    Args:
        num: Number to format.

    Returns:
        str: Formatted number string.
    """
    return f"{num:,}"


def format_percentage(value: float, decimals: int = 1) -> str:
    """
    Format a value as percentage.

    Args:
        value: Value to format.
        decimals: Number of decimal places.

    Returns:
        str: Formatted percentage string.
    """
    return f"{value:.{decimals}f}%"
