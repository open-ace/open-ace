"""
Open ACE - Quota Validation Schema

Provides validation and limits for quota values to ensure:
- Values fit within PostgreSQL INTEGER constraints
- Values are non-negative
- Values are valid numbers (not NaN)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict

from app.constants import EXPLICIT_NULL

if TYPE_CHECKING:
    from app.repositories.database import Database, adapt_boolean_condition
else:
    from app.repositories.database import adapt_boolean_condition

logger = logging.getLogger(__name__)

# Token quota limits (stored in M units)
# PostgreSQL INTEGER max: 2,147,483,647
# Since we store in M units, max is approximately 2,147 M tokens
MAX_TOKEN_QUOTA = 2147

# Request quota limits (stored as actual count)
MAX_REQUEST_QUOTA = 2147483647

# Minimum quota value
MIN_QUOTA = 0


class QuotaAllocationResult(TypedDict):
    """Type definition for tenant allocation validation result."""

    is_valid: bool
    error: str
    available: dict[str, int]
    is_unlimited_tenant: bool
    details: dict[str, int | str]  # Enhanced error details for better diagnostics


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
    new_daily_token_quota: int | None | object = None,
    new_monthly_token_quota: int | None | object = None,
    new_daily_request_quota: int | None | object = None,
    new_monthly_request_quota: int | None | object = None,
    db: Database | None = None,
) -> QuotaAllocationResult:
    """
    Validate tenant quota allocation to ensure user quotas don't exceed tenant limits.

    This function checks if allocating new quota values to a user would cause
    the total allocated quota to exceed the tenant's limits.

    Field semantics (important - distinguishes None from EXPLICIT_NULL):
        - None: No change, keep current value (skip validation for this field)
        - EXPLICIT_NULL: Set to unlimited (validate against tenant limits)
        - int value: Set to specified value (validate against tenant limits)

    Args:
        tenant_id: ID of the tenant.
        user_id: ID of the user being updated (None for new users).
        new_daily_token_quota: New daily token quota in M units.
        new_monthly_token_quota: New monthly token quota in M units.
        new_daily_request_quota: New daily request quota.
        new_monthly_request_quota: New monthly request quota.
        db: Database instance for queries.

    Returns:
        Dict with keys:
            - is_valid: bool - Whether the allocation is valid
            - error: Optional[str] - Error message key (i18n) if invalid
            - available: Dict with remaining available quota values
            - is_unlimited_tenant: bool - Whether tenant has unlimited quota
            - details: Dict with enhanced error information for diagnostics
    """
    # Import Database at runtime to avoid circular imports
    if db is None:
        from app.repositories.database import Database as DatabaseClass

        db = DatabaseClass()

    result: QuotaAllocationResult = {
        "is_valid": True,
        "error": "",
        "available": {
            "daily_token": 0,
            "monthly_token": 0,
            "daily_request": 0,
            "monthly_request": 0,
        },
        "is_unlimited_tenant": False,
        "details": {},
    }

    logger.debug(
        f"Validating tenant allocation: tenant_id={tenant_id}, user_id={user_id}, "
        f"daily_token={new_daily_token_quota}, monthly_token={new_monthly_token_quota}, "
        f"daily_request={new_daily_request_quota}, monthly_request={new_monthly_request_quota}"
    )

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

    # Decision D1: For limited tenants, reject unlimited user quota (EXPLICIT_NULL)
    # Important: None means "no change", EXPLICIT_NULL means "set to unlimited"
    # Only validate fields that are being modified (not None)
    
    # Check daily_token_quota
    if new_daily_token_quota is EXPLICIT_NULL and daily_token_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        result["details"] = {
            "field": "daily_token_quota",
            "reason": "explicit_null_not_allowed",
            "tenant_limit": daily_token_limit,
            "suggestion": "Provide a specific value or set tenant to unlimited",
        }
        logger.warning(
            f"Tenant {tenant_id} rejects unlimited daily_token_quota: tenant_limit={daily_token_limit}"
        )
        return result

    # Check monthly_token_quota
    if new_monthly_token_quota is EXPLICIT_NULL and monthly_token_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        result["details"] = {
            "field": "monthly_token_quota",
            "reason": "explicit_null_not_allowed",
            "tenant_limit": monthly_token_limit,
            "suggestion": "Provide a specific value or set tenant to unlimited",
        }
        logger.warning(
            f"Tenant {tenant_id} rejects unlimited monthly_token_quota: tenant_limit={monthly_token_limit}"
        )
        return result

    # Check daily_request_quota
    if new_daily_request_quota is EXPLICIT_NULL and daily_request_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        result["details"] = {
            "field": "daily_request_quota",
            "reason": "explicit_null_not_allowed",
            "tenant_limit": daily_request_limit,
            "suggestion": "Provide a specific value or set tenant to unlimited",
        }
        logger.warning(
            f"Tenant {tenant_id} rejects unlimited daily_request_quota: tenant_limit={daily_request_limit}"
        )
        return result

    # Check monthly_request_quota
    if new_monthly_request_quota is EXPLICIT_NULL and monthly_request_limit not in (None, 0):
        result["is_valid"] = False
        result["error"] = "Cannot set unlimited quota for a tenant with quota limits"
        result["details"] = {
            "field": "monthly_request_quota",
            "reason": "explicit_null_not_allowed",
            "tenant_limit": monthly_request_limit,
            "suggestion": "Provide a specific value or set tenant to unlimited",
        }
        logger.warning(
            f"Tenant {tenant_id} rejects unlimited monthly_request_quota: tenant_limit={monthly_request_limit}"
        )
        return result

    # Calculate currently allocated quota (excluding current user)
    # Each quota field handles NULL values independently using conditional aggregation
    # Token quotas are stored in M units
    allocated_row = db.fetch_one(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN daily_token_quota IS NOT NULL THEN daily_token_quota ELSE 0 END), 0) as daily_token,
            COALESCE(SUM(CASE WHEN monthly_token_quota IS NOT NULL THEN monthly_token_quota ELSE 0 END), 0) as monthly_token,
            COALESCE(SUM(CASE WHEN daily_request_quota IS NOT NULL THEN daily_request_quota ELSE 0 END), 0) as daily_request,
            COALESCE(SUM(CASE WHEN monthly_request_quota IS NOT NULL THEN monthly_request_quota ELSE 0 END), 0) as monthly_request
        FROM users
        WHERE tenant_id = ?
          AND {adapt_boolean_condition('is_active', True)}
          AND (id IS NULL OR id != ?)
    """,
        (tenant_id, user_id or 0),
    )

    allocated_daily_token = allocated_row.get("daily_token", 0) if allocated_row else 0
    allocated_monthly_token = allocated_row.get("monthly_token", 0) if allocated_row else 0
    allocated_daily_request = allocated_row.get("daily_request", 0) if allocated_row else 0
    allocated_monthly_request = allocated_row.get("monthly_request", 0) if allocated_row else 0

    # Calculate total allocation for validation
    # Important: Only validate if field is being modified (not None)
    # - None: No change, skip validation (return None)
    # - EXPLICIT_NULL: Unlimited, doesn't count against limit (total = allocated)
    # - int: Specific value, count against limit (total = allocated + new)

    def calculate_total(new_val: int | None | object, allocated: int) -> int | None:
        """Calculate total allocation for limit comparison.
        
        Returns:
            int: Total allocation to check against limit
            None: Skip validation (field not being modified)
        """
        if new_val is None:
            return None  # No change, skip validation
        if new_val is EXPLICIT_NULL:
            return allocated  # Unlimited user doesn't count against limit
        return allocated + new_val  # Add new user's quota to allocated

    total_daily_token = calculate_total(new_daily_token_quota, allocated_daily_token)
    total_monthly_token = calculate_total(new_monthly_token_quota, allocated_monthly_token)
    total_daily_request = calculate_total(new_daily_request_quota, allocated_daily_request)
    total_monthly_request = calculate_total(new_monthly_request_quota, allocated_monthly_request)

    # Token quotas are stored in M units, need to convert for comparison
    # Tenant limits are in actual token counts
    TOKEN_QUOTA_MULTIPLIER = 1_000_000

    # Helper function to build enhanced error details
    def build_quota_error_details(
        quota_type: str,
        tenant_limit: int,
        allocated: int,
        user_new_quota: int | None,
        multiplier: int = 1,
    ):
        """Build detailed error information for quota exceeded."""
        total = allocated * multiplier + (user_new_quota or 0) * multiplier
        over_by = total - tenant_limit
        return {
            "quota_type": quota_type,
            "tenant_limit": tenant_limit,
            "currently_allocated": allocated * multiplier,
            "user_new_quota": user_new_quota * multiplier if user_new_quota else None,
            "over_by": over_by,
            "suggestion": f"Reduce other users' quotas or increase tenant limit to at least {total}",
        }

    # Check daily token quota - only if being modified (total is not None)
    if total_daily_token is not None and daily_token_limit and daily_token_limit > 0:
        logger.debug(
            f"Validating daily_token: allocated={allocated_daily_token}M, "
            f"new={new_daily_token_quota}, limit={daily_token_limit}"
        )
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
            result["details"] = build_quota_error_details(
                "daily_token", daily_token_limit, allocated_daily_token,
                new_daily_token_quota if new_daily_token_quota is not EXPLICIT_NULL else None,
                TOKEN_QUOTA_MULTIPLIER,
            )
            logger.warning(
                f"Tenant {tenant_id} daily token quota exceeded: "
                f"allocated={allocated_daily_token}M, new={new_daily_token_quota}, limit={daily_token_limit}"
            )
            return result
        result["available"]["daily_token"] = max(
            0,
            (daily_token_limit - allocated_daily_token * TOKEN_QUOTA_MULTIPLIER)
            // TOKEN_QUOTA_MULTIPLIER,
        )

    # Check monthly token quota - only if being modified (total is not None)
    if total_monthly_token is not None and monthly_token_limit and monthly_token_limit > 0:
        logger.debug(
            f"Validating monthly_token: allocated={allocated_monthly_token}M, "
            f"new={new_monthly_token_quota}, limit={monthly_token_limit}"
        )
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
            result["details"] = build_quota_error_details(
                "monthly_token", monthly_token_limit, allocated_monthly_token,
                new_monthly_token_quota if new_monthly_token_quota is not EXPLICIT_NULL else None,
                TOKEN_QUOTA_MULTIPLIER,
            )
            logger.warning(
                f"Tenant {tenant_id} monthly token quota exceeded: "
                f"allocated={allocated_monthly_token}M, new={new_monthly_token_quota}, limit={monthly_token_limit}"
            )
            return result
        result["available"]["monthly_token"] = max(
            0,
            (monthly_token_limit - allocated_monthly_token * TOKEN_QUOTA_MULTIPLIER)
            // TOKEN_QUOTA_MULTIPLIER,
        )

    # Check daily request quota - only if being modified (total is not None)
    if total_daily_request is not None and daily_request_limit and daily_request_limit > 0:
        logger.debug(
            f"Validating daily_request: allocated={allocated_daily_request}, "
            f"new={new_daily_request_quota}, limit={daily_request_limit}"
        )
        if total_daily_request > daily_request_limit:
            available_daily = max(0, daily_request_limit - allocated_daily_request)
            result["is_valid"] = False
            result["error"] = "Tenant daily request quota exceeded"
            result["available"]["daily_request"] = available_daily
            result["details"] = build_quota_error_details(
                "daily_request", daily_request_limit, allocated_daily_request,
                new_daily_request_quota if new_daily_request_quota is not EXPLICIT_NULL else None,
            )
            logger.warning(
                f"Tenant {tenant_id} daily request quota exceeded: "
                f"allocated={allocated_daily_request}, new={new_daily_request_quota}, limit={daily_request_limit}"
            )
            return result
        result["available"]["daily_request"] = max(0, daily_request_limit - allocated_daily_request)

    # Check monthly request quota - only if being modified (total is not None)
    if total_monthly_request is not None and monthly_request_limit and monthly_request_limit > 0:
        logger.debug(
            f"Validating monthly_request: allocated={allocated_monthly_request}, "
            f"new={new_monthly_request_quota}, limit={monthly_request_limit}"
        )
        if total_monthly_request > monthly_request_limit:
            available_monthly = max(0, monthly_request_limit - allocated_monthly_request)
            result["is_valid"] = False
            result["error"] = "Tenant monthly request quota exceeded"
            result["available"]["monthly_request"] = available_monthly
            result["details"] = build_quota_error_details(
                "monthly_request", monthly_request_limit, allocated_monthly_request,
                new_monthly_request_quota if new_monthly_request_quota is not EXPLICIT_NULL else None,
            )
            logger.warning(
                f"Tenant {tenant_id} monthly request quota exceeded: "
                f"allocated={allocated_monthly_request}, new={new_monthly_request_quota}, limit={monthly_request_limit}"
            )
            return result
        result["available"]["monthly_request"] = max(
            0, monthly_request_limit - allocated_monthly_request
        )

    return result
