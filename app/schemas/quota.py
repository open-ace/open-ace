"""
Open ACE - Quota Validation Schema

Provides validation and limits for quota values to ensure:
- Values fit within PostgreSQL INTEGER constraints
- Values are non-negative
- Values are valid numbers (not NaN)
"""

from __future__ import annotations

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


def validate_request_quota(
    value: int | None, quota_name: str = "request_quota"
) -> tuple[bool, str]:
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


def validate_tenant_allocation(
    tenant_id: int,
    user_id: int | None = None,
    new_daily_token_quota: int | None = None,
    new_monthly_token_quota: int | None = None,
    new_daily_request_quota: int | None = None,
    new_monthly_request_quota: int | None = None,
    db: "Database | None" = None,
) -> dict:
    """
    Validate tenant quota allocation to ensure user quotas don't exceed tenant limits.

    This function checks if allocating new quota values to a user would cause
    the total allocated quota to exceed the tenant's limits.

    Args:
        tenant_id: ID of the tenant.
        user_id: ID of the user being updated (None for new users).
        new_daily_token_quota: New daily token quota in M units (None for unlimited).
        new_monthly_token_quota: New monthly token quota in M units (None for unlimited).
        new_daily_request_quota: New daily request quota (None for unlimited).
        new_monthly_request_quota: New monthly request quota (None for unlimited).
        db: Database instance for queries.

    Returns:
        Dict with keys:
            - is_valid: bool - Whether the allocation is valid
            - error: Optional[str] - Error message key (i18n) if invalid
            - available: Dict with remaining available quota values
            - is_unlimited_tenant: bool - Whether tenant has unlimited quota
    """
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from app.repositories.database import Database

    if db is None:
        from app.repositories.database import Database

        db = Database()

    result = {
        "is_valid": True,
        "error": None,
        "available": {
            "daily_token": 0,
            "monthly_token": 0,
            "daily_request": 0,
            "monthly_request": 0,
        },
        "is_unlimited_tenant": False,
    }

    # Get tenant quota limits from tenant_quotas table
    tenant_quota_row = db.fetch_one(
        """
        SELECT daily_token_limit, monthly_token_limit,
               daily_request_limit, monthly_request_limit
        FROM tenant_quotas
        WHERE tenant_id = ?
    """,
        (tenant_id,),
    )

    if not tenant_quota_row:
        # Tenant not found - return error
        result["is_valid"] = False
        result["error"] = "Tenant not found"
        return result

    # Check if tenant has unlimited quota (all limits are NULL or 0)
    daily_token_limit = tenant_quota_row.get("daily_token_limit")
    monthly_token_limit = tenant_quota_row.get("monthly_token_limit")
    daily_request_limit = tenant_quota_row.get("daily_request_limit")
    monthly_request_limit = tenant_quota_row.get("monthly_request_limit")

    # If tenant has unlimited quota (all limits are None or 0), allow any allocation
    if (
        (daily_token_limit is None or daily_token_limit == 0)
        and (monthly_token_limit is None or monthly_token_limit == 0)
        and (daily_request_limit is None or daily_request_limit == 0)
        and (monthly_request_limit is None or monthly_request_limit == 0)
    ):
        result["is_unlimited_tenant"] = True
        return result

    # Decision D1: For limited tenants, reject unlimited user quota (null values)
    # Exception: If the tenant has unlimited quota (handled above), null is allowed
    if new_daily_token_quota is None and daily_token_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        return result

    if new_monthly_token_quota is None and monthly_token_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        return result

    if new_daily_request_quota is None and daily_request_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        return result

    if new_monthly_request_quota is None and monthly_request_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        return result

    # Calculate currently allocated quota (excluding current user)
    # Each quota field handles NULL values independently using conditional aggregation
    # Token quotas are stored in M units
    allocated_row = db.fetch_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN daily_token_quota IS NOT NULL THEN daily_token_quota ELSE 0 END), 0) as daily_token,
            COALESCE(SUM(CASE WHEN monthly_token_quota IS NOT NULL THEN monthly_token_quota ELSE 0 END), 0) as monthly_token,
            COALESCE(SUM(CASE WHEN daily_request_quota IS NOT NULL THEN daily_request_quota ELSE 0 END), 0) as daily_request,
            COALESCE(SUM(CASE WHEN monthly_request_quota IS NOT NULL THEN monthly_request_quota ELSE 0 END), 0) as monthly_request
        FROM users
        WHERE tenant_id = ?
          AND is_active = 1
          AND (id IS NULL OR id != ?)
    """,
        (tenant_id, user_id or 0),
    )

    allocated_daily_token = allocated_row.get("daily_token", 0) if allocated_row else 0
    allocated_monthly_token = allocated_row.get("monthly_token", 0) if allocated_row else 0
    allocated_daily_request = allocated_row.get("daily_request", 0) if allocated_row else 0
    allocated_monthly_request = allocated_row.get("monthly_request", 0) if allocated_row else 0

    # Add new quota values to calculate total
    total_daily_token = allocated_daily_token + (new_daily_token_quota or 0)
    total_monthly_token = allocated_monthly_token + (new_monthly_token_quota or 0)
    total_daily_request = allocated_daily_request + (new_daily_request_quota or 0)
    total_monthly_request = allocated_monthly_request + (new_monthly_request_quota or 0)

    # Token quotas are stored in M units, need to convert for comparison
    # Tenant limits are in actual token counts
    TOKEN_QUOTA_MULTIPLIER = 1_000_000

    # Check daily token quota
    if daily_token_limit and daily_token_limit > 0:
        # Convert allocated (M units) to actual tokens for comparison
        total_daily_tokens_actual = total_daily_token * TOKEN_QUOTA_MULTIPLIER
        if total_daily_tokens_actual > daily_token_limit:
            available_daily = max(
                0,
                (daily_token_limit - allocated_daily_token * TOKEN_QUOTA_MULTIPLIER)
                // TOKEN_QUOTA_MULTIPLIER,
            )
            result["is_valid"] = False
            result["error"] = "Tenant daily token quota exceeded"
            result["available"]["daily_token"] = available_daily
            return result
        result["available"]["daily_token"] = max(
            0,
            (daily_token_limit - allocated_daily_token * TOKEN_QUOTA_MULTIPLIER)
            // TOKEN_QUOTA_MULTIPLIER,
        )

    # Check monthly token quota
    if monthly_token_limit and monthly_token_limit > 0:
        total_monthly_tokens_actual = total_monthly_token * TOKEN_QUOTA_MULTIPLIER
        if total_monthly_tokens_actual > monthly_token_limit:
            available_monthly = max(
                0,
                (monthly_token_limit - allocated_monthly_token * TOKEN_QUOTA_MULTIPLIER)
                // TOKEN_QUOTA_MULTIPLIER,
            )
            result["is_valid"] = False
            result["error"] = "Tenant monthly token quota exceeded"
            result["available"]["monthly_token"] = available_monthly
            return result
        result["available"]["monthly_token"] = max(
            0,
            (monthly_token_limit - allocated_monthly_token * TOKEN_QUOTA_MULTIPLIER)
            // TOKEN_QUOTA_MULTIPLIER,
        )

    # Check daily request quota
    if daily_request_limit and daily_request_limit > 0:
        if total_daily_request > daily_request_limit:
            available_daily = max(0, daily_request_limit - allocated_daily_request)
            result["is_valid"] = False
            result["error"] = "Tenant daily request quota exceeded"
            result["available"]["daily_request"] = available_daily
            return result
        result["available"]["daily_request"] = max(0, daily_request_limit - allocated_daily_request)

    # Check monthly request quota
    if monthly_request_limit and monthly_request_limit > 0:
        if total_monthly_request > monthly_request_limit:
            available_monthly = max(0, monthly_request_limit - allocated_monthly_request)
            result["is_valid"] = False
            result["error"] = "Tenant monthly request quota exceeded"
            result["available"]["monthly_request"] = available_monthly
            return result
        result["available"]["monthly_request"] = max(
            0, monthly_request_limit - allocated_monthly_request
        )

    return result
