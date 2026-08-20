"""Date Range Errors - error codes and localized messages for date validation.

Provides error code constants and message mapping with i18n support.

Issue #2738: Centralized error handling for date range validation.
"""

from __future__ import annotations

# Error code constants (lowercase snake_case style, matching existing patterns)
ERROR_INCOMPLETE_DATE_RANGE = "incomplete_date_range"
ERROR_INVALID_DATE_FORMAT = "invalid_date_format"
ERROR_INVALID_DATE_ORDER = "invalid_date_order"
ERROR_DATE_RANGE_EXCEEDED = "date_range_exceeded"
ERROR_FUTURE_DATE_NOT_ALLOWED = "future_date_not_allowed"
ERROR_INVALID_TIME_WINDOW = "invalid_time_window"

# Error message mapping (supports i18n)
DATE_RANGE_ERROR_MESSAGES: dict[str, dict[str, str]] = {
    ERROR_INCOMPLETE_DATE_RANGE: {
        "en": "Both start_date and end_date must be provided together",
        "zh": "必须同时提供开始日期和结束日期",
    },
    ERROR_INVALID_DATE_FORMAT: {
        "en": "Invalid date format. Expected YYYY-MM-DD",
        "zh": "日期格式无效，请使用 YYYY-MM-DD 格式",
    },
    ERROR_INVALID_DATE_ORDER: {
        "en": "start_date must be earlier than or equal to end_date",
        "zh": "开始日期必须早于或等于结束日期",
    },
    ERROR_DATE_RANGE_EXCEEDED: {
        "en": "Date range exceeds maximum of {max_days} days",
        "zh": "日期范围超过最大限制 {max_days} 天",
    },
    ERROR_FUTURE_DATE_NOT_ALLOWED: {
        "en": "Future dates are not allowed",
        "zh": "不允许查询未来日期",
    },
    ERROR_INVALID_TIME_WINDOW: {
        "en": "{param_name} must be between {min_val} and {max_val}",
        "zh": "{param_name} 必须在 {min_val} 到 {max_val} 之间",
    },
}


def get_error_message(error_code: str, **kwargs) -> str:
    """Get localized error message, auto-detecting language from request context.

    Language priority:
        1. Flask g.language (if set)
        2. Accept-Language request header
        3. Default 'en'

    Args:
        error_code: Error code constant
        **kwargs: Message template parameters (e.g., max_days, param_name)

    Returns:
        Localized error message string.
    """
    # Import here to avoid circular imports
    from flask import g, request

    # Language detection priority
    lang: str = getattr(g, "language", None)  # type: ignore[assignment]
    if lang is None:
        # Parse from Accept-Language header, support en/zh
        try:
            lang = request.accept_languages.best_match(["en", "zh"], "en")  # type: ignore[assignment]
        except RuntimeError:
            # Outside request context (e.g., testing)
            lang = "en"

    messages = DATE_RANGE_ERROR_MESSAGES.get(error_code, {})
    template = messages.get(lang, messages.get("en", "Invalid date range"))
    return template.format(**kwargs) if kwargs else template


__all__ = [
    "ERROR_INCOMPLETE_DATE_RANGE",
    "ERROR_INVALID_DATE_FORMAT",
    "ERROR_INVALID_DATE_ORDER",
    "ERROR_DATE_RANGE_EXCEEDED",
    "ERROR_FUTURE_DATE_NOT_ALLOWED",
    "ERROR_INVALID_TIME_WINDOW",
    "DATE_RANGE_ERROR_MESSAGES",
    "get_error_message",
]
