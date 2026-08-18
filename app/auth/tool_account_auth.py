"""
Open ACE - Tool Account Authorization Module

Authorization helpers for tool account mapping management.
Provides centralized tenant boundary checks for tenant_admin and platform_admin roles.

Issue #2759: Centralized authorization for tool account management interfaces.
"""

import logging
from typing import Any

from flask import g

from app.auth.permissions import is_platform_level_role
from app.repositories.user_repo import UserRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository

logger = logging.getLogger(__name__)

user_repo = UserRepository()
tool_account_repo = UserToolAccountRepository()


def validate_user_in_tenant(user_id: int, tenant_id: int) -> bool:
    """
    Validate that a tenant admin may operate on ``user_id``.

    Issue #2180: Ensures tenant admin can only operate on users in their tenant.
    Issue #2759: Shared with mapping_rules.py for consistent authorization.

    The role check is not redundant with the tenant check. Comparing tenants
    alone is horizontal-only, and a platform-level account can perfectly well
    carry a tenant id -- ``api_create_user`` requires one, and the schema only
    forces ``tenant_admin`` to have it. Without this, a tenant admin can act on
    a ``platform_admin`` filed under its own tenant. Mirrors
    ``app.auth.decorators.enforce_target_user_tenant``.

    Args:
        user_id: Target user ID to validate.
        tenant_id: Tenant ID that the actor belongs to.

    Returns:
        True if user exists, is not platform-level, and belongs to the tenant.
    """
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return False
    if is_platform_level_role(user.get("role")):
        return False
    user_tenant_id = user.get("tenant_id")
    return user_tenant_id == tenant_id


def get_tenant_scoped_user_ids(tenant_id: int) -> list[int]:
    """
    Get list of user IDs belonging to a tenant.

    Issue #2180: Used for filtering by tenant.
    Issue #2759: Shared with mapping_rules.py for consistent authorization.

    Args:
        tenant_id: Tenant ID to filter users by.

    Returns:
        List of user IDs belonging to the tenant.
    """
    users = user_repo.get_all_users(tenant_id=tenant_id)
    return [u["id"] for u in users]


def get_mapping_and_validate_tenant(
    mapping_id: int, tenant_id: int | None
) -> tuple[Any, dict | None, str | None]:
    """
    Get a mapping by ID and validate it belongs to the actor's tenant.

    Issue #2759: Resource-level authorization for update/delete operations.

    For tenant_admin: validates the mapping's user belongs to the same tenant.
    For platform_admin: no tenant validation, returns the mapping directly.

    Args:
        mapping_id: The mapping ID to fetch.
        tenant_id: The actor's tenant ID (None for platform_admin).

    Returns:
        Tuple of (mapping, user, error_response):
        - mapping: The UserToolAccount object or None
        - user: The user dict that owns the mapping, or None
        - error_response: Error message string if validation failed, None otherwise

    Note:
        Returns (None, None, None) for "not found" to allow caller to return 404.
        Returns (None, None, error_message) for cross-tenant access (caller should return 404).
    """
    mapping = tool_account_repo.get_by_id(mapping_id)
    if not mapping:
        return None, None, None

    # Get the user that owns this mapping
    user = user_repo.get_user_by_id(mapping.user_id)

    # Platform admin (tenant_id is None) - no tenant validation
    if tenant_id is None:
        return mapping, user, None

    # Tenant admin - validate tenant boundary
    if not user:
        # User no longer exists, deny access
        logger.warning(
            "Mapping %s references non-existent user %s",
            mapping_id,
            mapping.user_id,
        )
        return None, None, "Mapping user not found"

    # Check if user belongs to actor's tenant
    user_tenant_id = user.get("tenant_id")
    if user_tenant_id != tenant_id:
        # Log the cross-tenant attempt for audit
        logger.info(
            "Cross-tenant mapping access blocked: actor_tenant=%s, mapping_user_tenant=%s, "
            "mapping_id=%s, user_id=%s",
            tenant_id,
            user_tenant_id,
            mapping_id,
            mapping.user_id,
        )
        return None, None, "Cross-tenant access denied"

    # Check if user is platform-level (vertical check)
    if is_platform_level_role(user.get("role")):
        logger.warning(
            "Tenant admin denied operation on platform-level account mapping: "
            "mapping_id=%s, user_id=%s, user_role=%s",
            mapping_id,
            user.get("id"),
            user.get("role"),
        )
        return None, None, "Cannot modify platform-level account mapping"

    return mapping, user, None


def validate_target_user_for_write(
    target_user_id: int, actor_tenant_id: int | None
) -> tuple[dict | None, str | None]:
    """
    Validate a target user for write operations (create/update).

    Issue #2759: Pre-write authorization check for tool account mapping.

    For tenant_admin: validates the target user belongs to the same tenant
                     and is not platform-level.
    For platform_admin: no tenant validation, user must exist.

    Args:
        target_user_id: The user ID to validate.
        actor_tenant_id: The actor's tenant ID (None for platform_admin).

    Returns:
        Tuple of (user_dict, error_response):
        - user_dict: The user dict if valid, None otherwise
        - error_response: Error message string if validation failed, None otherwise
    """
    user = user_repo.get_user_by_id(target_user_id)
    if not user:
        return None, "User not found"

    # Platform admin (tenant_id is None) - no tenant validation
    if actor_tenant_id is None:
        return user, None

    # Tenant admin - validate tenant boundary
    user_tenant_id = user.get("tenant_id")
    if user_tenant_id != actor_tenant_id:
        logger.info(
            "Cross-tenant write blocked: actor_tenant=%s, target_user_tenant=%s, "
            "target_user_id=%s",
            actor_tenant_id,
            user_tenant_id,
            target_user_id,
        )
        return None, "Cannot create mapping for user in different tenant"

    # Check if user is platform-level (vertical check)
    if is_platform_level_role(user.get("role")):
        logger.warning(
            "Tenant admin denied write on platform-level account: "
            "target_user_id=%s, user_role=%s",
            target_user_id,
            user.get("role"),
        )
        return None, "Cannot create mapping for platform-level account"

    return user, None