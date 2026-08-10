"""
Security Mode API - Unified security mode detection and validation.

This module provides the single source of truth for security mode across
all startup paths: Docker entrypoint, Python app factory, CLI, systemd,
and Kubernetes deployments.

Issue #2185: Unified security mode definition and key validation.
Issue #2331: Explicit mode enforcement with fail-closed behavior.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)


class SecurityMode(Enum):
    """Security mode enumeration."""

    PRODUCTION = "production"
    PILOT = "pilot"
    DEVELOPMENT = "development"


class SecurityModeSource(Enum):
    """Source of security mode detection.

    Issue #2331: Track how security mode was determined.
    """

    EXPLICIT = "explicit"  # From OPENACE_SECURITY_MODE
    INFERRED_FLASK_ENV = "inferred"  # From FLASK_ENV (deprecated, remove in v2.1.0)
    DEFAULT = "default"  # Development fallback (restricted to non-production paths)


# Module-level cache (thread-safe via Python's import lock)
# Issue #2331: Initialize at module level for thread safety
_mode_cache: SecurityMode | None = None
_source_cache: SecurityModeSource | None = None
_initialized: bool = False

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


def is_test_context() -> bool:
    """
    Check if running in test context.

    Issue #2331: Multi-layer test detection to avoid race conditions.

    Checks (any match returns True):
    - pytest or unittest in sys.modules
    - OPENACE_TEST_MODE=1 environment variable
    - PYTEST_CURRENT_TEST environment variable (set by pytest)

    Returns:
        True if running in test context, False otherwise.
    """
    # Layer 1: sys.modules check
    if "pytest" in sys.modules or "unittest" in sys.modules:
        return True

    # Layer 2: Explicit test mode environment variable
    if os.environ.get("OPENACE_TEST_MODE") == "1":
        return True

    # Layer 3: Pytest sets this during test execution
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True

    return False


def is_production_capable_path() -> bool:
    """
    Determine if current execution path is production-capable.

    Issue #2331: Explicit criteria for production-capable paths.

    Production-capable paths require explicit OPENACE_SECURITY_MODE.

    Returns:
        True if production-capable, False otherwise.
    """
    # Test context is never production-capable
    if is_test_context():
        return False

    # CI environments are not production-capable
    if os.environ.get("CI") == "true":
        return False
    if os.environ.get("FLASK_ENV") == "test":
        return False

    # Emergency rollback flag (expires after 30 days)
    # Requires OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP to enforce expiration
    emergency_rollback = os.environ.get("OPENACE_ALLOW_IMPLICIT_MODE")
    if emergency_rollback == "1":
        # Check for required timestamp
        timestamp_str = os.environ.get("OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP")

        if not timestamp_str:
            logger.error(
                "EMERGENCY ROLLBACK FLAG EXPIRED: OPENACE_ALLOW_IMPLICIT_MODE=1 requires "
                "OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP to be set. "
                "Format: YYYY-MM-DD (e.g., 2025-01-15). "
                "Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
            )
            # No timestamp - flag is invalid, continue with normal checks
        else:
            # Parse and validate timestamp
            try:
                from datetime import datetime, timedelta

                # Parse timestamp (format: YYYY-MM-DD)
                flag_date = datetime.strptime(timestamp_str, "%Y-%m-%d")
                now = datetime.now()
                age_days = (now - flag_date).days

                if age_days > 30:
                    logger.error(
                        f"EMERGENCY ROLLBACK FLAG EXPIRED: OPENACE_ALLOW_IMPLICIT_MODE was set "
                        f"{age_days} days ago (max: 30 days). "
                        f"Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
                    )
                    # Flag expired, continue with normal checks
                else:
                    logger.warning(
                        f"EMERGENCY ROLLBACK: OPENACE_ALLOW_IMPLICIT_MODE=1 is active "
                        f"(set {age_days} days ago, expires in {30 - age_days} days). "
                        "This flag should not be used in production. "
                        "Set OPENACE_SECURITY_MODE explicitly instead."
                    )
                    # Flag is valid and within 30-day window
                    return False
            except ValueError as e:
                logger.error(
                    f"EMERGENCY ROLLBACK FLAG INVALID: OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP "
                    f"format error: {e}. Expected format: YYYY-MM-DD. "
                    f"Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
                )
                # Invalid timestamp format, flag is ignored

    # Check for production indicators
    # 1. Explicit mode requested
    if os.environ.get("OPENACE_SECURITY_MODE"):
        return True

    # 2. Scheduler mode (web or scheduler, not dev)
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "")
    if scheduler_mode in ("web", "scheduler"):
        return True

    # 3. Flask production environment
    if os.environ.get("FLASK_ENV") == "production":
        return True

    # 4. Running under systemd
    if os.path.exists("/run/systemd/system"):
        return True

    # 5. Running in Kubernetes
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True

    # Default: not production-capable (local development, REPL, etc.)
    return False


def detect_security_mode() -> tuple[SecurityMode, SecurityModeSource]:
    """
    Detect security mode based on environment variables.

    Issue #2331: Enhanced to return source and enforce explicit mode.

    Priority:
    1. OPENACE_SECURITY_MODE (explicit setting)
    2. FLASK_ENV=production (deprecated, remove in v2.1.0)
    3. Fail in production-capable paths, allow default in non-production paths

    Returns:
        Tuple of (SecurityMode, SecurityModeSource)

    Raises:
        RuntimeError: When mode is unknown or missing in production-capable path.
    """
    # Priority 1: Explicit OPENACE_SECURITY_MODE
    mode_env = os.environ.get("OPENACE_SECURITY_MODE", "").strip().lower()

    if mode_env:
        if mode_env == "production":
            return SecurityMode.PRODUCTION, SecurityModeSource.EXPLICIT
        if mode_env == "pilot":
            return SecurityMode.PILOT, SecurityModeSource.EXPLICIT
        if mode_env == "development":
            return SecurityMode.DEVELOPMENT, SecurityModeSource.EXPLICIT
        # Unknown mode - fail closed
        raise RuntimeError(
            f"Unknown OPENACE_SECURITY_MODE value: '{mode_env}'. "
            f"Valid values: production, pilot, development. "
            f"Set OPENACE_SECURITY_MODE explicitly in your environment."
        )

    # Priority 2: FLASK_ENV=production (DEPRECATED - remove in v2.1.0)
    flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
    if flask_env == "production":
        logger.warning(
            "DEPRECATED: FLASK_ENV=production fallback will be removed in v2.1.0. "
            "Set OPENACE_SECURITY_MODE=production explicitly. "
            "Migration guide: https://github.com/open-ace/open-ace/issues/2331"
        )
        return SecurityMode.PRODUCTION, SecurityModeSource.INFERRED_FLASK_ENV

    # Priority 3: No mode set - check if production-capable path
    if is_production_capable_path():
        raise RuntimeError(
            "OPENACE_SECURITY_MODE must be set explicitly in production-capable paths. "
            "Valid values: production, pilot, development.\n"
            "Examples:\n"
            "  - Production: OPENACE_SECURITY_MODE=production\n"
            "  - Pilot/Trial: OPENACE_SECURITY_MODE=pilot\n"
            "  - Development: OPENACE_SECURITY_MODE=development\n\n"
            "For local development, run: python server.py\n"
            "For Docker: set in docker-compose.yml or .env\n"
            "For Kubernetes: set in ConfigMap\n"
            "For systemd: set in service unit file"
        )

    # Default: development mode for non-production paths
    # (local development, REPL, etc.)
    logger.info(
        "Security mode not set. Defaulting to development mode for local execution. "
        "Set OPENACE_SECURITY_MODE explicitly for production deployments."
    )
    return SecurityMode.DEVELOPMENT, SecurityModeSource.DEFAULT


def get_security_mode() -> SecurityMode:
    """
    Get the current security mode (cached after first detection).

    This function caches the result to ensure consistency within a process.

    Returns:
        SecurityMode: The current security mode.

    Raises:
        RuntimeError: When mode is missing or unknown.
    """
    global _mode_cache, _source_cache, _initialized

    if not _initialized:
        _mode_cache, _source_cache = detect_security_mode()
        _initialized = True

    return _mode_cache  # type: ignore


def get_security_mode_with_source() -> tuple[SecurityMode, SecurityModeSource]:
    """
    Get the current security mode and its source.

    Issue #2331: Expose mode source for health endpoints.

    Returns:
        Tuple of (SecurityMode, SecurityModeSource)

    Raises:
        RuntimeError: When mode is missing or unknown.
    """
    global _mode_cache, _source_cache, _initialized

    if not _initialized:
        _mode_cache, _source_cache = detect_security_mode()
        _initialized = True

    return _mode_cache, _source_cache  # type: ignore


def require_explicit_mode() -> None:
    """
    Require explicit security mode in production-capable paths.

    Issue #2331: Entry points call this before any application logic.

    Raises:
        RuntimeError: In production-capable paths without explicit mode.
    """
    global _mode_cache, _source_cache, _initialized

    # Test context: allow implicit development
    if is_test_context():
        if not _initialized:
            _mode_cache = SecurityMode.DEVELOPMENT
            _source_cache = SecurityModeSource.DEFAULT
            _initialized = True
        return

    # Production-capable path: require explicit mode
    if is_production_capable_path():
        # Check if mode is already cached
        if _initialized:
            # Already validated, just check source
            if _source_cache == SecurityModeSource.EXPLICIT:
                return
            # Inferred or default - not allowed in production paths
            raise RuntimeError(
                f"Security mode must be explicitly set in production-capable paths. "
                f"Current source: {_source_cache.value}. "
                f"Set OPENACE_SECURITY_MODE explicitly."
            )

        # Not initialized - run full detection (will raise if needed)
        _mode_cache, _source_cache = detect_security_mode()
        _initialized = True
        return

    # Non-production path: allow implicit development
    if not _initialized:
        _mode_cache, _source_cache = detect_security_mode()
        _initialized = True


def reset_security_mode_cache() -> None:
    """Reset the security mode cache (for testing)."""
    global _mode_cache, _source_cache, _initialized
    _mode_cache = None
    _source_cache = None
    _initialized = False


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


def get_pilot_metadata_path() -> str:
    """
    Get path to pilot mode metadata file.

    Issue #2331: Track pilot mode metadata for observability.

    Returns:
        Path to pilot-mode-metadata.json
    """
    config_dir = os.environ.get("OPENACE_CONFIG_DIR")
    if not config_dir:
        config_dir = os.path.join(os.path.expanduser("~"), ".open-ace")
    return os.path.join(config_dir, "pilot-mode-metadata.json")


def create_pilot_metadata(secrets_generated: list[str]) -> dict:
    """
    Create pilot mode metadata for observability.

    Issue #2331: Track auto-generated secrets in pilot mode.

    Args:
        secrets_generated: List of secret names that were auto-generated.

    Returns:
        Metadata dictionary to be written to file.
    """
    metadata = {
        "mode": "pilot",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "warning": "NOT FOR PRODUCTION USE - Auto-generated secrets",
        "secrets_generated": secrets_generated,
        "persistent_file": "generated-secrets.env",
    }

    metadata_path = get_pilot_metadata_path()

    # Write metadata file
    try:
        import json

        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info("Pilot mode metadata written to %s", metadata_path)
    except Exception as e:
        logger.warning("Failed to write pilot metadata: %s", e)

    return metadata


def load_pilot_metadata() -> dict | None:
    """
    Load pilot mode metadata if it exists.

    Issue #2331: Check for pilot metadata in production mode.

    Returns:
        Metadata dictionary if file exists, None otherwise.
    """
    metadata_path = get_pilot_metadata_path()

    if not os.path.exists(metadata_path):
        return None

    try:
        import json

        with open(metadata_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to read pilot metadata: %s", e)
        return None