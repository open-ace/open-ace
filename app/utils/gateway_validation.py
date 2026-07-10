"""
Gateway configuration input validation.

Provides validation functions for model gateway configuration fields
to prevent injection attacks and ensure data integrity.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from flask import abort


class ValidationError(Exception):
    """Raised when input validation fails."""

    pass


def validate_base_url(url: str) -> str:
    """Validate gateway base URL.

    Args:
        url: The base URL to validate.

    Returns:
        The validated URL (trimmed).

    Raises:
        ValidationError: If the URL is invalid.
    """
    if not url:
        raise ValidationError("base_url is required")

    url = url.strip()

    # Check URL format
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValidationError("Invalid URL format")

    # Must be http or https
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("URL must use http or https scheme")

    # Must have a hostname
    if not parsed.hostname:
        raise ValidationError("URL must have a hostname")

    # Validate port if specified
    if parsed.port is not None:
        if not (1 <= parsed.port <= 65535):
            raise ValidationError("Port must be between 1 and 65535")

    # Check for path traversal attempts
    dangerous_patterns = ["..", "~", "\\", "//"]
    for pattern in dangerous_patterns:
        if pattern in url:
            raise ValidationError(f"URL contains forbidden pattern: {pattern}")

    # Check for control characters
    if any(ord(c) < 32 for c in url):
        raise ValidationError("URL contains control characters")

    return url


def validate_api_key(key: str) -> str:
    """Validate gateway API key.

    Args:
        key: The API key to validate.

    Returns:
        The validated API key.

    Raises:
        ValidationError: If the API key is invalid.
    """
    if not key:
        raise ValidationError("api_key is required")

    # Length check
    if len(key) < 8:
        raise ValidationError("api_key must be at least 8 characters")
    if len(key) > 512:
        raise ValidationError("api_key must be at most 512 characters")

    # Character set check (allow letters, numbers, dash, underscore)
    allowed_pattern = re.compile(r"^[a-zA-Z0-9\-_]+$")
    if not allowed_pattern.match(key):
        raise ValidationError("api_key contains invalid characters (only letters, numbers, -, _ allowed)")

    # Check for control characters
    if any(ord(c) < 32 for c in key):
        raise ValidationError("api_key contains control characters")

    return key


def validate_model_prefix(prefix: str | None) -> str | None:
    """Validate model prefix.

    Args:
        prefix: The model prefix to validate (optional).

    Returns:
        The validated prefix (or None).

    Raises:
        ValidationError: If the prefix is invalid.
    """
    if not prefix:
        return None

    prefix = prefix.strip()

    # Length check
    if len(prefix) < 1:
        raise ValidationError("model_prefix must be at least 1 character")
    if len(prefix) > 64:
        raise ValidationError("model_prefix must be at most 64 characters")

    # Character set check
    allowed_pattern = re.compile(r"^[a-zA-Z0-9\-_/.]+$")
    if not allowed_pattern.match(prefix):
        raise ValidationError(
            "model_prefix contains invalid characters (only letters, numbers, -, _, /, . allowed)"
        )

    # Check for control characters
    if any(ord(c) < 32 for c in prefix):
        raise ValidationError("model_prefix contains control characters")

    return prefix


def mask_api_key(key: str | None) -> str | None:
    """Mask API key for display.

    Shows only first 7 and last 4 characters, with *** in between.
    Example: sk-1234***5678

    Args:
        key: The API key to mask.

    Returns:
        The masked API key, or None if key is None/empty.
    """
    if not key:
        return None

    if len(key) <= 11:
        # Key too short to meaningfully mask
        return f"{key[:3]}***"

    prefix = key[:7]
    suffix = key[-4:]
    return f"{prefix}***{suffix}"


def validate_enabled_value(enabled: bool) -> bool:
    """Validate enabled field value.

    Args:
        enabled: The enabled value to validate.

    Returns:
        The validated enabled value.

    Raises:
        ValidationError: If enabled is not a boolean.
    """
    if not isinstance(enabled, bool):
        raise ValidationError("enabled must be a boolean")
    return enabled