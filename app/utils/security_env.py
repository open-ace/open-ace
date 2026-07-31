"""
Helpers for validating security-sensitive environment variables.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_WEAK_SECRET_VALUES = frozenset(
    {
        "",
        "change-me-in-production",
        "dev-secret-key",
        "dev-smtp-password-key",
        "default-secret-key",
    }
)

# Prefixes used by committed deployment manifests (k8s/configmap.yaml) as
# placeholders the operator must replace. Matching the prefix (rather than
# each literal string) keeps a future manifest from silently reintroducing a
# new ``replace-with-random-*`` value that passes the weak-secret check.
_WEAK_SECRET_PREFIXES = ("replace-with-random",)

_DEV_SECRET_KEY = "dev-secret-key"  # nosec B105 - explicit development-only fallback
_DEV_ENCRYPTION_KEY = (  # nosec B105 - explicit development-only fallback
    "openace-dev-encryption-key"
)


def is_production_environment() -> bool:
    """Return whether the current process is running in production mode."""
    return os.environ.get("FLASK_ENV", "development").strip().lower() == "production"


def is_strict_mode() -> bool:
    """
    Check if strict key validation is enabled.

    Strict mode:
    - Enabled by default in production (FLASK_ENV=production)
    - Can be overridden via OPENACE_STRICT_KEY_VALIDATION env var
    - In strict mode, weak keys raise RuntimeError
    - In non-strict mode, weak keys log warnings

    Returns:
        True if strict mode is enabled, False otherwise.
    """
    # Check explicit override
    override = os.environ.get("OPENACE_STRICT_KEY_VALIDATION", "").strip().lower()
    if override in ("true", "1", "yes"):
        return True
    if override in ("false", "0", "no"):
        return False

    # Default to production mode
    return is_production_environment()


def is_weak_secret_value(value: str | None) -> bool:
    """Return whether the given secret value is missing or a known placeholder."""
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in _WEAK_SECRET_VALUES:
        return True
    return any(normalized.startswith(prefix) for prefix in _WEAK_SECRET_PREFIXES)


def validate_secret_strength(
    value: str | None, purpose: str, *, min_length: int = 32
) -> None:
    """
    Validate that a secret key meets minimum strength requirements.

    Checks:
    1. Key is not empty
    2. Key is not in denylist (weak values)
    3. Key length >= min_length

    In strict mode (production), raises RuntimeError on failure.
    In non-strict mode, logs warning on failure.

    Args:
        value: The secret value to validate.
        purpose: Description of the key's purpose (for error messages).
        min_length: Minimum required length (default 32).

    Raises:
        RuntimeError: In strict mode when validation fails.
    """
    # Check for empty value
    if not value:
        if is_strict_mode():
            raise RuntimeError(
                f"{purpose} environment variable must be set in production!"
            )
        logger.warning(
            "%s is empty; this is insecure for production use", purpose
        )
        return

    # Check for weak values
    if is_weak_secret_value(value):
        if is_strict_mode():
            raise RuntimeError(
                f"{purpose} must be set to a strong, unique value in production!"
            )
        logger.warning(
            "%s uses a weak development value - DO NOT use in production!",
            purpose,
        )
        return

    # Check minimum length
    if len(value) < min_length:
        if is_strict_mode():
            raise RuntimeError(
                f"{purpose} must be at least {min_length} characters long "
                f"(got {len(value)}); current value is too short (strict mode)"
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


def get_secret_key_for_app(secret_key: str | None = None) -> str:
    """Return a validated Flask SECRET_KEY."""
    if secret_key is None:
        secret_key = os.environ.get("SECRET_KEY")

    # Handle empty value with fallback
    if not secret_key:
        if is_strict_mode():
            raise RuntimeError("SECRET_KEY environment variable must be set in production!")
        logger.warning("Using development SECRET_KEY - DO NOT use in production!")
        return _DEV_SECRET_KEY

    # Validate strength
    try:
        validate_secret_strength(secret_key, "SECRET_KEY", min_length=32)
    except RuntimeError:
        # Re-raise in strict mode
        raise

    return secret_key


def get_encryption_key_material(*, purpose: str) -> str:
    """Return validated key material for encrypted secret storage."""
    key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")

    # Handle empty value with fallback
    if not key_env or is_weak_secret_value(key_env):
        if is_strict_mode():
            raise RuntimeError(
                f"OPENACE_ENCRYPTION_KEY must be set to a strong, unique value in production for {purpose} (strict mode)"
            )
        logger.warning(
            "OPENACE_ENCRYPTION_KEY not set; using development-only encryption key for %s. "
            "Encrypted data will not be portable across environments.",
            purpose,
        )
        return _DEV_ENCRYPTION_KEY

    # Validate strength
    try:
        validate_secret_strength(key_env, "OPENACE_ENCRYPTION_KEY", min_length=32)
    except RuntimeError:
        # Re-raise in strict mode
        raise

    return key_env


def get_upload_auth_key() -> str | None:
    """Return a validated upload auth key, or None when upload endpoints should stay disabled."""
    upload_auth_key = os.environ.get("UPLOAD_AUTH_KEY")
    if not upload_auth_key:
        return None

    # Check for weak values - always reject
    if is_weak_secret_value(upload_auth_key):
        logger.error(
            "UPLOAD_AUTH_KEY uses an insecure placeholder value; upload endpoints disabled"
        )
        return None

    # Validate strength in strict mode
    try:
        validate_secret_strength(upload_auth_key, "UPLOAD_AUTH_KEY", min_length=32)
    except RuntimeError as e:
        logger.error(str(e))
        return None

    return upload_auth_key


_DEV_REDIS_PASSWORD = "dev-redis-password"  # nosec B105 - explicit development-only fallback


def get_redis_password(redis_password: str | None = None) -> str:
    """
    Return a validated Redis password.

    In production, empty or weak passwords are rejected (fail-closed).
    In development, a development-only password is used with a warning.

    Args:
        redis_password: Optional password override. If None, reads from REDIS_PASSWORD env.

    Returns:
        A validated Redis password.

    Raises:
        RuntimeError: In production when password is empty or weak.
    """
    if redis_password is None:
        redis_password = os.environ.get("REDIS_PASSWORD")

    # Handle empty value with fallback
    if not redis_password:
        if is_strict_mode():
            raise RuntimeError("REDIS_PASSWORD must be set in production (strict mode)")
        logger.warning("Using development REDIS_PASSWORD - DO NOT use in production!")
        return _DEV_REDIS_PASSWORD

    # Validate strength (min_length=24 for Redis)
    try:
        validate_secret_strength(redis_password, "REDIS_PASSWORD", min_length=24)
    except RuntimeError:
        # Re-raise in strict mode
        raise

    return redis_password
