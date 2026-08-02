"""
Security Mode API - Unified security mode detection and validation.

This module provides the single source of truth for security mode across
all startup paths: Docker entrypoint, Python app factory, CLI, systemd,
and Kubernetes deployments.

Issue #2185: Unified security mode definition and key validation.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)


class SecurityMode(Enum):
    """Security mode enumeration."""

    PRODUCTION = "production"
    PILOT = "pilot"
    DEVELOPMENT = "development"


# Cache for the detected security mode (process-wide singleton)
_cached_security_mode: SecurityMode | None = None


# Known weak/placeholder secret values (shared across security modules)
WEAK_SECRET_VALUES: Final[frozenset[str]] = frozenset(
    {
        "",
        "change-me-in-production",
        "dev-secret-key",
        "dev-smtp-password-key",
        "default-secret-key",
        "openace-dev-encryption-key",
        "dev-redis-password",
    }
)

# Prefixes used by committed deployment manifests as placeholders
WEAK_SECRET_PREFIXES: Final[tuple[str, ...]] = ("replace-with-random",)


def detect_security_mode() -> SecurityMode:
    """
    Detect security mode based on environment variables.

    Priority:
    1. OPENACE_SECURITY_MODE (explicit setting)
    2. FLASK_ENV=production (backward compatibility)
    3. Default: development (backward compatibility with docker-entrypoint.sh)

    Returns:
        SecurityMode: The detected security mode.

    Raises:
        RuntimeError: When mode is unknown (invalid value).
    """
    # Priority 1: Explicit OPENACE_SECURITY_MODE
    mode_env = os.environ.get("OPENACE_SECURITY_MODE", "").strip().lower()

    if mode_env:
        if mode_env == "production":
            return SecurityMode.PRODUCTION
        if mode_env == "pilot":
            return SecurityMode.PILOT
        if mode_env == "development":
            return SecurityMode.DEVELOPMENT
        # Unknown mode - fail closed
        raise RuntimeError(
            f"Unknown OPENACE_SECURITY_MODE value: '{mode_env}'. "
            f"Valid values: production, pilot, development. "
            f"Set OPENACE_SECURITY_MODE explicitly in your environment."
        )

    # Priority 2: FLASK_ENV=production (backward compatibility)
    flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
    if flask_env == "production":
        logger.warning(
            "Security mode inferred from FLASK_ENV=production. "
            "Consider setting OPENACE_SECURITY_MODE=production explicitly. "
            "FLASK_ENV inference may be removed in a future version."
        )
        return SecurityMode.PRODUCTION

    # Default: development mode (backward compatibility with docker-entrypoint.sh)
    # Matches Shell behavior: "Default: development mode"
    logger.warning(
        "Security mode not configured. Defaulting to development mode. "
        "Set OPENACE_SECURITY_MODE explicitly for production deployments. "
        "Examples:\n"
        "  - Production: OPENACE_SECURITY_MODE=production\n"
        "  - Pilot/Trial: OPENACE_SECURITY_MODE=pilot\n"
        "  - Development: OPENACE_SECURITY_MODE=development\n"
        "For Docker Compose, add to .env or docker-compose.yml environment section."
    )
    return SecurityMode.DEVELOPMENT


def get_security_mode() -> SecurityMode:
    """
    Get the current security mode (cached after first detection).

    This function caches the result to ensure consistency within a process.

    Returns:
        SecurityMode: The current security mode.

    Raises:
        RuntimeError: When mode is missing or unknown.
    """
    global _cached_security_mode
    if _cached_security_mode is None:
        _cached_security_mode = detect_security_mode()
    return _cached_security_mode


def reset_security_mode_cache() -> None:
    """Reset the security mode cache (for testing)."""
    global _cached_security_mode
    _cached_security_mode = None


def is_production() -> bool:
    """Check if running in production mode."""
    return get_security_mode() == SecurityMode.PRODUCTION


def is_pilot() -> bool:
    """Check if running in pilot mode."""
    return get_security_mode() == SecurityMode.PILOT


def is_development() -> bool:
    """Check if running in development mode."""
    return get_security_mode() == SecurityMode.DEVELOPMENT


def is_strict_mode() -> bool:
    """
    Check if strict key validation is enabled.

    Strict mode is enabled ONLY in production mode.
    The OPENACE_STRICT_KEY_VALIDATION override is NO LONGER supported
    for bypassing production key requirements (Issue #2185).

    Returns:
        True if strict mode is enabled (production only).
    """
    return is_production()


def is_weak_secret_value(value: str | None) -> bool:
    """
    Return whether the given secret value is missing or a known placeholder.

    Args:
        value: The secret value to check.

    Returns:
        True if the value is weak/placeholder, False otherwise.
    """
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in WEAK_SECRET_VALUES:
        return True
    return any(normalized.startswith(prefix) for prefix in WEAK_SECRET_PREFIXES)


def validate_secret_strength(value: str | None, purpose: str, *, min_length: int = 32) -> None:
    """
    Validate that a secret key meets minimum strength requirements.

    Checks:
    1. Key is not empty
    2. Key is not in denylist (weak values)
    3. Key length >= min_length

    In production mode, raises RuntimeError on failure.
    In pilot/development mode, logs warning on failure.

    Args:
        value: The secret value to validate.
        purpose: Description of the key's purpose (for error messages).
        min_length: Minimum required length (default 32).

    Raises:
        RuntimeError: In production mode when validation fails.
    """
    mode = get_security_mode()

    # Check for empty value
    if not value:
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                f"{purpose} environment variable must be set in production mode! "
                f'Generate: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        logger.warning("%s is empty; this is insecure for production use", purpose)
        return

    # Check for weak values
    if is_weak_secret_value(value):
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                f"{purpose} must be set to a strong, unique value in production! "
                f"Detected weak/placeholder value. "
                f'Generate: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        logger.warning(
            "%s uses a weak development value - DO NOT use in production!",
            purpose,
        )
        return

    # Check minimum length
    if len(value) < min_length:
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                f"{purpose} must be at least {min_length} characters long "
                f"(got {len(value)}); current value is too short for production"
            )
        logger.warning(
            "%s is shorter than recommended minimum length %d (got %d)",
            purpose,
            min_length,
            len(value),
        )
        return

    # All checks passed
    logger.debug("%s meets strength requirements", purpose)
