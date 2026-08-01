"""
Request Context Utilities for Issue #2163

Provides centralized helpers for accessing request context data
like current user and tenant.
"""

from typing import TypedDict, cast

from flask import g
from werkzeug.exceptions import BadRequest


class UserContext(TypedDict, total=False):
    """User context type definition."""

    id: int
    username: str
    tenant_id: int | None
    role: str
    tenant_version: int | None


def get_current_user() -> UserContext | None:
    """
    Get the current request's user context.

    Returns:
        User context dictionary or None if not authenticated
    """
    user = getattr(g, "user", None)
    if user and isinstance(user, dict):
        return cast("UserContext | None", user)
    return None


def get_current_tenant_id() -> int | None:
    """
    Get the current authenticated user's tenant ID.

    Returns:
        Tenant ID or None if not available
    """
    user = get_current_user()
    if not user:
        return None
    return user.get("tenant_id")


def require_tenant_id() -> int:
    """
    Get the current tenant ID, raising error if not available.

    Returns:
        Tenant ID

    Raises:
        BadRequest: If tenant context is not available
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        raise BadRequest("Tenant context required")
    return tenant_id


def get_current_tenant_version() -> int | None:
    """
    Get the current authenticated user's tenant version.

    Returns:
        Tenant version or None if not available
    """
    user = get_current_user()
    if not user:
        return None
    return user.get("tenant_version")


def get_current_user_id() -> int | None:
    """
    Get the current authenticated user's ID.

    Returns:
        User ID or None if not authenticated
    """
    user = get_current_user()
    if not user:
        return None
    return user.get("id")
