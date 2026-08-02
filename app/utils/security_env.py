"""
Helpers for validating security-sensitive environment variables.

This module provides secret key validation and retrieval functions.
Security mode detection is centralized in security_mode.py (Issue #2185).

All functions use the unified SecurityMode API from security_mode.py.
"""

from __future__ import annotations

import logging
import os

from app.utils.security_mode import (
    SecurityMode,
    get_security_mode,
    is_production,
    is_strict_mode,
    is_weak_secret_value,
    validate_secret_strength,
)

logger = logging.getLogger(__name__)


def get_secret_key_for_app(secret_key: str | None = None) -> str:
    """
    Return a validated Flask SECRET_KEY.

    In production mode:
    - Secret key MUST be set explicitly via environment variable.
    - Empty, weak, or short keys raise RuntimeError.
    - No fallback to development keys.

    In pilot mode:
    - Allows auto-generation with strong warnings.
    - Should persist to generated-secrets.env.

    In development mode:
    - Allows auto-generation, persisted to local file.
    - Ensures consistency across restarts and workers.

    Args:
        secret_key: Optional secret key override. If None, reads from SECRET_KEY env.

    Returns:
        A validated secret key.

    Raises:
        RuntimeError: In production mode when key is missing or invalid.
    """
    if secret_key is None:
        secret_key = os.environ.get("SECRET_KEY")

    mode = get_security_mode()

    # Handle empty value
    if not secret_key:
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production mode! "
                "Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        # For pilot/development, caller should handle auto-generation
        # (typically done in docker-entrypoint.sh)
        logger.warning(
            "SECRET_KEY not set. In %s mode, this should be auto-generated and persisted.",
            mode.value,
        )
        raise RuntimeError(
            f"SECRET_KEY not set in {mode.value} mode. "
            f"The entrypoint should auto-generate and persist this key. "
            f"If running directly (not via entrypoint), set SECRET_KEY explicitly."
        )

    # Validate strength (raises RuntimeError in production if invalid)
    validate_secret_strength(secret_key, "SECRET_KEY", min_length=32)

    return secret_key


def get_encryption_key_material(*, purpose: str) -> str:
    """
    Return validated key material for encrypted secret storage.

    In production mode:
    - Encryption key MUST be set explicitly.
    - Required for encrypting SMTP passwords, API keys, etc.

    In pilot mode:
    - Allows auto-generation with strong warnings.
    - Data encrypted with auto-generated key is not portable.

    In development mode:
    - Allows auto-generation, persisted to local file.
    - Encrypted data portable only within same secret store.

    Args:
        purpose: Description of what the key is used for (for messages).

    Returns:
        A validated encryption key.

    Raises:
        RuntimeError: In production mode when key is missing or invalid.
    """
    key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
    mode = get_security_mode()

    # Handle empty value
    if not key_env or is_weak_secret_value(key_env):
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                f"OPENACE_ENCRYPTION_KEY must be set to a strong, unique value in production for {purpose}! "
                f"Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        logger.warning(
            "OPENACE_ENCRYPTION_KEY not set in %s mode. "
            "This should be auto-generated and persisted. "
            "Encrypted data will not be portable across environments.",
            mode.value,
        )
        raise RuntimeError(
            f"OPENACE_ENCRYPTION_KEY not set in {mode.value} mode. "
            f"The entrypoint should auto-generate and persist this key. "
            f"If running directly, set OPENACE_ENCRYPTION_KEY explicitly."
        )

    # Validate strength (raises RuntimeError in production if invalid)
    validate_secret_strength(key_env, "OPENACE_ENCRYPTION_KEY", min_length=32)

    return key_env


def get_upload_auth_key() -> str | None:
    """
    Return a validated upload auth key, or None when upload endpoints should stay disabled.

    In production mode:
    - If upload endpoints are needed, key MUST be set explicitly.
    - Weak/placeholder keys are rejected.

    In pilot/development mode:
    - Allows auto-generation if needed.

    Returns:
        A validated upload auth key, or None if not set.

    Raises:
        RuntimeError: In production mode when key is set but invalid.
    """
    upload_auth_key = os.environ.get("UPLOAD_AUTH_KEY")
    if not upload_auth_key:
        return None

    mode = get_security_mode()

    # Check for weak values - always reject
    if is_weak_secret_value(upload_auth_key):
        logger.error(
            "UPLOAD_AUTH_KEY uses an insecure placeholder value; upload endpoints disabled"
        )
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                "UPLOAD_AUTH_KEY uses an insecure placeholder value in production! "
                "Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return None

    # Validate strength in production mode
    validate_secret_strength(upload_auth_key, "UPLOAD_AUTH_KEY", min_length=32)

    return upload_auth_key


def get_redis_password(redis_password: str | None = None) -> str:
    """
    Return a validated Redis password.

    In production mode:
    - Password MUST be set explicitly.
    - Empty or weak passwords raise RuntimeError.

    In pilot mode:
    - Allows auto-generation with strong warnings.

    In development mode:
    - Allows auto-generation, persisted to local file.

    Args:
        redis_password: Optional password override. If None, reads from REDIS_PASSWORD env.

    Returns:
        A validated Redis password.

    Raises:
        RuntimeError: In production mode when password is missing or invalid.
    """
    if redis_password is None:
        redis_password = os.environ.get("REDIS_PASSWORD")

    mode = get_security_mode()

    # Handle empty value
    if not redis_password:
        if mode == SecurityMode.PRODUCTION:
            raise RuntimeError(
                "REDIS_PASSWORD must be set in production mode! "
                "Generate: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        logger.warning(
            "REDIS_PASSWORD not set in %s mode. "
            "This should be auto-generated and persisted.",
            mode.value,
        )
        raise RuntimeError(
            f"REDIS_PASSWORD not set in {mode.value} mode. "
            f"The entrypoint should auto-generate and persist this password. "
            f"If running directly, set REDIS_PASSWORD explicitly."
        )

    # Validate strength (raises RuntimeError in production if invalid)
    validate_secret_strength(redis_password, "REDIS_PASSWORD", min_length=24)

    return redis_password


# Re-export for backward compatibility (used by tests and other modules)
__all__ = [
    "SecurityMode",
    "get_security_mode",
    "is_production",
    "is_strict_mode",
    "is_weak_secret_value",
    "validate_secret_strength",
    "get_secret_key_for_app",
    "get_encryption_key_material",
    "get_upload_auth_key",
    "get_redis_password",
]