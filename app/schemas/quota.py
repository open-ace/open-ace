"""
Open ACE - Quota Validation Schema

Provides validation and limits for quota values to ensure:
- Values fit within PostgreSQL INTEGER constraints
- Values are non-negative
- Values are valid numbers (not NaN)
"""

import logging

logger = logging.getLogger(__name__)

# Token quota limits (stored in M units)
# PostgreSQL INTEGER max: 2,147,483,647
# Since we store in M units, max is approximately 2,147 M tokens
MAX_TOKEN_QUOTA = 2147

# Request quota limits (stored as actual count)
MAX_REQUEST_QUOTA = 2147483647

# Minimum quota value
MIN_QUOTA = 0


def validate_token_quota(value: int | None, quota_name: str = "token_quota") -> tuple[bool, str]:
    """
    Validate a token quota value.

    Args:
        value: Quota value in M units (or None for unlimited)
        quota_name: Name of the quota field for error messages

    Returns:
        Tuple of (is_valid, error_message)
    """
    # None means unlimited, which is valid
    if value is None:
        return True, ""

    # Check for non-integer types
    if not isinstance(value, int):
        try:
            # Try to convert to int
            value = int(value)
        except (ValueError, TypeError):
            return False, f"{quota_name} must be an integer"

    # Check for negative values
    if value < MIN_QUOTA:
        return False, f"{quota_name} cannot be negative"

    # Check for exceeding database limit
    if value > MAX_TOKEN_QUOTA:
        return False, f"{quota_name} exceeds maximum limit of {MAX_TOKEN_QUOTA}M tokens"

    return True, ""


def validate_request_quota(value: int | None, quota_name: str = "request_quota") -> tuple[bool, str]:
    """
    Validate a request quota value.

    Args:
        value: Quota value as actual count (or None for unlimited)
        quota_name: Name of the quota field for error messages

    Returns:
        Tuple of (is_valid, error_message)
    """
    # None means unlimited, which is valid
    if value is None:
        return True, ""

    # Check for non-integer types
    if not isinstance(value, int):
        try:
            # Try to convert to int
            value = int(value)
        except (ValueError, TypeError):
            return False, f"{quota_name} must be an integer"

    # Check for negative values
    if value < MIN_QUOTA:
        return False, f"{quota_name} cannot be negative"

    # Check for exceeding database limit
    if value > MAX_REQUEST_QUOTA:
        return False, f"{quota_name} exceeds maximum limit of {MAX_REQUEST_QUOTA} requests"

    return True, ""


def validate_quota_update(
    daily_token_quota: int | None = None,
    monthly_token_quota: int | None = None,
    daily_request_quota: int | None = None,
    monthly_request_quota: int | None = None,
) -> tuple[bool, dict[str, str]]:
    """
    Validate all quota fields for an update request.

    Args:
        daily_token_quota: Daily token quota in M units
        monthly_token_quota: Monthly token quota in M units
        daily_request_quota: Daily request quota
        monthly_request_quota: Monthly request quota

    Returns:
        Tuple of (is_valid, errors_dict)
    """
    errors = {}

    # Validate daily token quota
    is_valid, error_msg = validate_token_quota(daily_token_quota, "daily_token_quota")
    if not is_valid:
        errors["daily_token_quota"] = error_msg
        logger.warning(f"Quota validation failed: {error_msg} (value: {daily_token_quota})")

    # Validate monthly token quota
    is_valid, error_msg = validate_token_quota(monthly_token_quota, "monthly_token_quota")
    if not is_valid:
        errors["monthly_token_quota"] = error_msg
        logger.warning(f"Quota validation failed: {error_msg} (value: {monthly_token_quota})")

    # Validate daily request quota
    is_valid, error_msg = validate_request_quota(daily_request_quota, "daily_request_quota")
    if not is_valid:
        errors["daily_request_quota"] = error_msg
        logger.warning(f"Quota validation failed: {error_msg} (value: {daily_request_quota})")

    # Validate monthly request quota
    is_valid, error_msg = validate_request_quota(monthly_request_quota, "monthly_request_quota")
    if not is_valid:
        errors["monthly_request_quota"] = error_msg
        logger.warning(f"Quota validation failed: {error_msg} (value: {monthly_request_quota})")

    return len(errors) == 0, errors


def get_quota_limits() -> dict:
    """
    Get quota limits configuration.

    Returns:
        Dictionary with quota limits
    """
    return {
        "token_quota": {
            "min": MIN_QUOTA,
            "max": MAX_TOKEN_QUOTA,
            "unit": "M",
            "description": "Token quotas are stored in M (millions) units",
        },
        "request_quota": {
            "min": MIN_QUOTA,
            "max": MAX_REQUEST_QUOTA,
            "unit": "",
            "description": "Request quotas are stored as actual counts",
        },
    }