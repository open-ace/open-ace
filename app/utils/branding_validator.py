"""
Branding validation utilities for Issue #3271.

Provides validation for logo URLs (SSRF protection), brand names (XSS protection),
and welcome messages.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.utils.outbound_url_guard import validate_public_http_url

logger = logging.getLogger(__name__)

# Supported languages for branding welcome messages
SUPPORTED_LANGUAGES = {"en", "zh", "ja", "ko"}

# Maximum lengths for branding fields
MAX_SYSTEM_NAME_LENGTH = 100
MAX_WELCOME_MESSAGE_LENGTH = 500
MAX_COPYRIGHT_LENGTH = 200

# XSS dangerous patterns
XSS_PATTERNS = [
    r"<script",  # Script tags
    r"javascript:",  # JavaScript protocol
    r"onerror\s*=",  # Event handlers
    r"onload\s*=",
    r"onclick\s*=",
    r"onmouseover\s*=",
    r"<iframe",  # Iframe tags
    r"<embed",  # Embed tags
    r"<object",  # Object tags
]


def validate_logo_url(url: str | None) -> tuple[bool, str]:
    """Validate a logo URL for SSRF protection.

    Args:
        url: The logo URL to validate (can be None or empty)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not url:
        # Empty or None URLs are allowed (will use default)
        return True, ""

    # Must be HTTPS only
    if not url.startswith("https://"):
        return False, "Logo URL must use HTTPS protocol"

    # Check for dangerous protocols
    dangerous_protocols = ["data:", "file:", "javascript:", "vbscript:"]
    for proto in dangerous_protocols:
        if url.lower().startswith(proto):
            return False, f"Logo URL cannot use {proto.rstrip(':')} protocol"

    # Use SSRF validation from outbound_url_guard
    result = validate_public_http_url(url)
    if not result.allowed:
        return False, f"Logo URL validation failed: {result.error}"

    return True, ""


def validate_brand_name(name: str | None) -> tuple[bool, str]:
    """Validate a brand name for XSS protection and length.

    Args:
        name: The brand name to validate (can be None or empty)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name:
        # Empty or None names are allowed (will use default)
        return True, ""

    # Length check
    if len(name) > MAX_SYSTEM_NAME_LENGTH:
        return False, f"Brand name must not exceed {MAX_SYSTEM_NAME_LENGTH} characters"

    # XSS check - reject dangerous patterns
    name_lower = name.lower()
    for pattern in XSS_PATTERNS:
        if re.search(pattern, name_lower, re.IGNORECASE):
            return False, "Brand name contains forbidden HTML or script elements"

    return True, ""


def validate_welcome_message(message: dict[str, str] | None) -> tuple[bool, str]:
    """Validate a welcome message for XSS protection and length.

    Args:
        message: The welcome message dict (language -> text), can be None or empty

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not message:
        # Empty or None messages are allowed (will use default)
        return True, ""

    if not isinstance(message, dict):
        return False, "Welcome message must be a dictionary"

    for lang, text in message.items():
        # Validate language code
        if lang not in SUPPORTED_LANGUAGES:
            logger.warning(f"Unsupported language code in welcome message: {lang}")
            # Don't reject, just warn - allow flexibility

        if not isinstance(text, str):
            return False, f"Welcome message for language '{lang}' must be a string"

        # Length check
        if len(text) > MAX_WELCOME_MESSAGE_LENGTH:
            return False, (
                f"Welcome message for language '{lang}' must not exceed "
                f"{MAX_WELCOME_MESSAGE_LENGTH} characters"
            )

        # XSS check
        text_lower = text.lower()
        for pattern in XSS_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return False, (
                    f"Welcome message for language '{lang}' contains "
                    f"forbidden HTML or script elements"
                )

    return True, ""


def validate_copyright_text(text: str | None) -> tuple[bool, str]:
    """Validate copyright text for XSS protection and length.

    Args:
        text: The copyright text to validate (can be None or empty)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not text:
        # Empty or None text is allowed (will use default)
        return True, ""

    # Length check
    if len(text) > MAX_COPYRIGHT_LENGTH:
        return False, f"Copyright text must not exceed {MAX_COPYRIGHT_LENGTH} characters"

    # XSS check
    text_lower = text.lower()
    for pattern in XSS_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return False, "Copyright text contains forbidden HTML or script elements"

    return True, ""


def validate_branding_settings(
    logo_url: str | None = None,
    system_name: str | None = None,
    welcome_message: dict[str, str] | None = None,
    copyright_text: str | None = None,
) -> tuple[bool, list[str]]:
    """Validate all branding settings at once.

    Args:
        logo_url: Logo URL to validate
        system_name: System name to validate
        welcome_message: Welcome message dict to validate
        copyright_text: Copyright text to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []

    # Validate logo URL
    if logo_url is not None:
        is_valid, error = validate_logo_url(logo_url)
        if not is_valid:
            errors.append(error)

    # Validate system name
    if system_name is not None:
        is_valid, error = validate_brand_name(system_name)
        if not is_valid:
            errors.append(error)

    # Validate welcome message
    if welcome_message is not None:
        is_valid, error = validate_welcome_message(welcome_message)
        if not is_valid:
            errors.append(error)

    # Validate copyright text
    if copyright_text is not None:
        is_valid, error = validate_copyright_text(copyright_text)
        if not is_valid:
            errors.append(error)

    return len(errors) == 0, errors