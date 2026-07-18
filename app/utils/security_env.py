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


def is_weak_secret_value(value: str | None) -> bool:
    """Return whether the given secret value is missing or a known placeholder."""
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in _WEAK_SECRET_VALUES:
        return True
    return any(normalized.startswith(prefix) for prefix in _WEAK_SECRET_PREFIXES)


def get_secret_key_for_app(secret_key: str | None = None) -> str:
    """Return a validated Flask SECRET_KEY."""
    if secret_key is None:
        secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if is_production_environment():
            raise RuntimeError("SECRET_KEY environment variable must be set in production!")
        logger.warning("Using development SECRET_KEY - DO NOT use in production!")
        return _DEV_SECRET_KEY

    if is_production_environment() and is_weak_secret_value(secret_key):
        raise RuntimeError("SECRET_KEY must be set to a strong, unique value in production!")

    if is_weak_secret_value(secret_key):
        logger.warning("Using weak development SECRET_KEY - DO NOT use in production!")

    return secret_key


def get_encryption_key_material(*, purpose: str) -> str:
    """Return validated key material for encrypted secret storage."""
    key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
    if key_env and not is_weak_secret_value(key_env):
        return key_env

    if is_production_environment():
        raise RuntimeError(
            f"OPENACE_ENCRYPTION_KEY must be set to a strong, unique value in production for {purpose}."
        )

    logger.warning(
        "OPENACE_ENCRYPTION_KEY not set; using development-only encryption key for %s. "
        "Encrypted data will not be portable across environments.",
        purpose,
    )
    return _DEV_ENCRYPTION_KEY


def get_upload_auth_key() -> str | None:
    """Return a validated upload auth key, or None when upload endpoints should stay disabled."""
    upload_auth_key = os.environ.get("UPLOAD_AUTH_KEY")
    if not upload_auth_key:
        return None

    if is_weak_secret_value(upload_auth_key):
        logger.error(
            "UPLOAD_AUTH_KEY uses an insecure placeholder value; upload endpoints disabled"
        )
        return None

    return upload_auth_key
