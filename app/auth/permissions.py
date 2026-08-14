"""
Centralized permission checking utilities.

All role checks should go through this module to ensure consistency
and centralized feature flag integration.

Issue #2332: Centralized platform admin checking with strict mode support.
"""

from __future__ import annotations

import logging
import os

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

    Scope, so nobody over-trusts this flag: it governs the checks that route
    through is_platform_admin_role / User.is_platform_admin -- notably
    platform_admin_required, same_tenant_or_platform_admin and
    resolve_admin_tenant_scope. It does NOT reach code that compares role
    strings directly, e.g. admin_required's own allow-list or
    app/routes/mapping_rules.py, which gates on `user_role == "tenant_admin"`
    and so leaves a legacy admin with unrestricted reach there. Enabling the
    flag narrows authorization; it does not make the legacy role disappear.

    Deployment Notes:
        1. Migrate every remaining ``role='admin'`` account to
           ``role='platform_admin'`` FIRST -- in strict mode a legacy admin
           stops being a platform admin and loses access.
        2. Set OPENACE_PLATFORM_ADMIN_STRICT_MODE=true
        3. Restart all application processes to pick up the new value
        4. Verify: each process logs "Platform admin strict mode: ENABLED"
           once at startup (see init_platform_admin_strict_mode).

    Issue #2332: Feature flag for phased rollout.
    """
    global _PLATFORM_ADMIN_STRICT_MODE

    if _PLATFORM_ADMIN_STRICT_MODE is None:
        # Read once at first access, then cache
        _PLATFORM_ADMIN_STRICT_MODE = (
            os.environ.get("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "false").lower() == "true"
        )

        # Log the mode at startup
        logger.info(
            "Platform admin strict mode: %s (set OPENACE_PLATFORM_ADMIN_STRICT_MODE=true to enable)",
            "ENABLED" if _PLATFORM_ADMIN_STRICT_MODE else "DISABLED",
        )

    return _PLATFORM_ADMIN_STRICT_MODE


def get_cached_strict_mode() -> bool:
    """Get the strict mode value, resolving the cache on first access.

    Used by decorators and utility functions to ensure consistent behavior
    across all checks within a single process.

    This delegates to :func:`is_platform_admin_strict_mode` so the flag is
    populated on first use. Before this delegation existed the function read
    the module global directly and returned ``_PLATFORM_ADMIN_STRICT_MODE or
    False``; because nothing in production ever called
    ``is_platform_admin_strict_mode``, the global stayed ``None`` forever and
    every consumer saw ``False``. ``OPENACE_PLATFORM_ADMIN_STRICT_MODE=true``
    had no effect at all -- the flag was dead code while reading as wired up.

    Call :func:`init_platform_admin_strict_mode` at application startup so the
    value is frozen (and logged) before the first request rather than on
    whichever request happens to check a role first.
    """
    return is_platform_admin_strict_mode()


def init_platform_admin_strict_mode() -> bool:
    """Resolve and freeze the strict mode flag at application startup.

    Idempotent: the first call reads the environment variable and caches it;
    later calls return the cached value. Called from ``create_app`` so the
    documented "cached at application startup" contract is actually true and
    the mode is logged once at boot.

    Returns:
        bool: the resolved strict mode value.
    """
    return is_platform_admin_strict_mode()


def warn_if_strict_mode_locks_out_legacy_admins() -> int | None:
    """Shout at startup if enabling strict mode is about to lock people out.

    This flag spent its whole life as dead code: nothing populated the cache,
    so ``OPENACE_PLATFORM_ADMIN_STRICT_MODE=true`` did nothing. Meanwhile
    ``docs/admin_role_migration_runbook_2332.md`` step 5 told operators to set
    exactly that variable. Anyone who followed the runbook has it exported and
    has seen no effect -- so the deploy that finally wires the flag up is the
    deploy where every remaining ``role='admin'`` account silently stops being
    a platform admin.

    Best-effort and never fatal: a missing table or unreachable database just
    skips the check.

    Returns:
        int | None: number of legacy admin rows found, or None if not checked.
    """
    if not get_cached_strict_mode():
        return None

    try:
        from app.repositories.database import Database

        rows = Database().fetch_one(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND deleted_at IS NULL"
        )
        count = int(rows["n"]) if rows else 0
    except Exception as exc:  # pragma: no cover - startup diagnostics only
        logger.debug("Could not count legacy admin accounts: %s", exc)
        return None

    if count:
        logger.error(
            "Platform admin strict mode is ENABLED but %d account(s) still have "
            "role='admin'. Those accounts are NO LONGER platform admins and will "
            "get 403 on platform-admin endpoints. Migrate them to "
            "role='platform_admin' or unset OPENACE_PLATFORM_ADMIN_STRICT_MODE. "
            "See docs/admin_role_migration_runbook_2332.md.",
            count,
        )
    return count


def reset_strict_mode_cache() -> None:
    """Clear the cached strict mode flag. Test-only.

    Production code must not call this: the cache exists precisely so that a
    mid-flight environment change cannot make two requests in the same process
    disagree about who counts as a platform admin.
    """
    global _PLATFORM_ADMIN_STRICT_MODE
    _PLATFORM_ADMIN_STRICT_MODE = None


# Roles that carry reach across every tenant. Unlike is_platform_admin_role
# this is NOT affected by strict mode: it answers "would this account be
# platform-level under any configuration", which is the right question when
# deciding whether a tenant admin may manage or hand out the role. Using the
# flag-sensitive check there would silently narrow protection the moment strict
# mode is enabled.
PLATFORM_LEVEL_ROLES: tuple[str, ...] = ("platform_admin", "admin")


def is_platform_level_role(role: str | None) -> bool:
    """Check if a role is platform-level regardless of strict mode.

    Args:
        role: User role string to check

    Returns:
        bool: True for 'platform_admin' or the legacy 'admin'.
    """
    return role in PLATFORM_LEVEL_ROLES


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
