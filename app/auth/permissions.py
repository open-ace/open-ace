"""
Centralized permission checking utilities.

All role checks should go through this module to ensure consistency
and centralized feature flag integration.

Issue #2332: Centralized platform admin checking with strict mode support.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

# Cache at module import time (checked once at startup)
# This prevents race conditions during deployment where different processes
# might have different feature flag states.
_PLATFORM_ADMIN_STRICT_MODE: bool | None = None


def is_platform_admin_strict_mode() -> bool:
    """Check if platform admin authorization should be strict.

    IMPORTANT: This value is cached at application startup for the entire
    process lifetime. Changing the environment variable requires process restart.

    This prevents race conditions during deployment where different processes
    might have different feature flag states.

    Returns:
        bool: True if strict mode is enabled (only accept explicit platform_admin).
              False if legacy admin is accepted for backward compatibility.

    Deployment Notes:
        1. Set OPENACE_PLATFORM_ADMIN_STRICT_MODE=true before deployment
        2. Deploy new code with strict mode enabled
        3. Restart all application processes to pick up new value
        4. Verify strict mode is active via /api/system/config endpoint

    Issue #2332: Feature flag for phased rollout.
    """
    global _PLATFORM_ADMIN_STRICT_MODE

    if _PLATFORM_ADMIN_STRICT_MODE is None:
        # Read once at first access, then cache
        _PLATFORM_ADMIN_STRICT_MODE = (
            os.environ.get("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "false")
            .lower() == "true"
        )

        # Log the mode at startup
        logger.info(
            "Platform admin strict mode: %s (set OPENACE_PLATFORM_ADMIN_STRICT_MODE=true to enable)",
            "ENABLED" if _PLATFORM_ADMIN_STRICT_MODE else "DISABLED"
        )

    return _PLATFORM_ADMIN_STRICT_MODE


def get_cached_strict_mode() -> bool:
    """Get the cached strict mode value without re-reading env var.

    Used by decorators and utility functions to ensure consistent behavior
    across all checks within a single process.
    """
    return _PLATFORM_ADMIN_STRICT_MODE or False


def is_platform_admin_role(role: str | None) -> bool:
    """Check if role represents platform admin.

    Respects strict mode feature flag:
    - Strict: Only role='platform_admin' returns True
    - Non-strict: role='platform_admin' or 'admin' returns True

    Args:
        role: User role string to check

    Returns:
        bool: True if role is platform admin (per strict mode setting)

    Issue #2332: Centralized platform admin checking.
    """
    if role is None:
        return False

    strict_mode = get_cached_strict_mode()

    if strict_mode:
        return role == "platform_admin"
    else:
        return role in ("platform_admin", "admin")


def is_tenant_admin_role(role: str | None) -> bool:
    """Check if role is tenant admin.

    Tenant admin must have tenant_id (enforced by database constraint).

    Args:
        role: User role string to check

    Returns:
        bool: True if role='tenant_admin'
    """
    return role == "tenant_admin"


def is_any_admin_role(role: str | None) -> bool:
    """Check if role is any admin variant.

    Use for non-cross-tenant operations where admin level doesn't matter.

    Args:
        role: User role string to check

    Returns:
        bool: True if role is platform_admin, tenant_admin, or legacy admin
    """
    return role in ("platform_admin", "tenant_admin", "admin")
