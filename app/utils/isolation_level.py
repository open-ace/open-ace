"""Utility functions for isolation level management (Issue #3427).

This module provides helper functions to reduce code duplication
when getting isolation_level from WebUIInstance.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_webui_instance(user_id: int | None) -> Any:
    """Get the live WebUIInstance for a user, or None.

    Single lookup path for callers that need the instance itself (e.g. #3459
    sandbox-side mkdir needs sandbox_id + launcher), not just its level.

    Args:
        user_id: User ID to look up WebUIInstance.

    Returns:
        The WebUIInstance, or None (no manager / no instance / lookup error).
    """
    if not user_id:
        return None

    try:
        from app.services.webui_manager import get_webui_manager

        manager = get_webui_manager()
        if not manager:
            return None

        return manager.get_user_instance(user_id)
    except Exception as e:
        # A real failure (import error, DB error) silently downgrades the
        # caller to host-side path semantics — the exact class of bug #3427
        # reports — so it must be visible at default log level.
        logger.warning("Failed to get WebUIInstance for user %s: %s", user_id, e)
        return None


def get_isolation_level_from_webui(user_id: int | None) -> str | None:
    """Get isolation_level from WebUIInstance.

    This function provides a centralized way to get isolation_level,
    reducing code duplication across multiple API endpoints.

    Args:
        user_id: User ID to look up WebUIInstance.

    Returns:
        Isolation level string (e.g., "sandboxed", "os_user") or None.
    """
    instance = get_webui_instance(user_id)
    return instance.isolation_level if instance is not None else None


def is_sandboxed(isolation_level: str | None) -> bool:
    """Whether an isolation level denotes the sandboxed form.

    Single comparison point for the ISOLATION_LEVEL_SANDBOXED constant so
    route code does not each import the contract module (Issue #3427).

    Args:
        isolation_level: Isolation level string or None.

    Returns:
        True only for the exact sandboxed level.
    """
    from app.services.workspace_isolation_contract import ISOLATION_LEVEL_SANDBOXED

    return isolation_level == ISOLATION_LEVEL_SANDBOXED


def get_user_isolation_level(user: dict[str, Any] | None) -> str | None:
    """Get isolation_level from user dict.

    Convenience wrapper that extracts user_id from user dict.

    Args:
        user: User dict with 'id' field.

    Returns:
        Isolation level string or None.
    """
    if not user:
        return None

    user_id = user.get("id")
    return get_isolation_level_from_webui(user_id)
