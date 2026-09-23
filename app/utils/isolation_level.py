"""Utility functions for isolation level management (Issue #3427).

This module provides helper functions to reduce code duplication
when getting isolation_level from WebUIInstance.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_isolation_level_from_webui(user_id: int | None) -> str | None:
    """Get isolation_level from WebUIInstance.

    This function provides a centralized way to get isolation_level,
    reducing code duplication across multiple API endpoints.

    Args:
        user_id: User ID to look up WebUIInstance.

    Returns:
        Isolation level string (e.g., "sandboxed", "os_user") or None.
    """
    if not user_id:
        return None

    try:
        from app.services.webui_manager import get_webui_manager

        manager = get_webui_manager()
        if not manager:
            return None

        instance = manager.get_user_instance(user_id)
        if not instance:
            return None

        return instance.isolation_level
    except Exception as e:
        # Log exception instead of silently swallowing it
        logger.debug("Failed to get isolation_level for user %s: %s", user_id, e)
        return None


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
