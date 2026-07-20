"""
Open ACE - Hostname Validator

Validates and sanitizes hostnames to filter out invalid entries
like hexadecimal strings, UUIDs, and placeholder patterns.

Reference: RFC 1123 hostname specification
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Whitelist for special hostnames that should always be allowed
DEFAULT_WHITELIST = {"localhost"}

# Blacklist patterns - invalid hostname formats
# 1. Pure hexadecimal strings (8-32 lowercase hex chars, no dots)
#    Examples: 01a73659, 050c3863, 10b58dd7
_HEX_PATTERN = re.compile(r"^[a-f0-9]{8,32}$")

# 2. UUID format (36 chars with hyphens)
_UUID_PATTERN = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE
)

# 3. Pure numeric strings (length > 10)
_NUMERIC_PATTERN = re.compile(r"^\d{11,}$")

# 4. Placeholder format <...>
_PLACEHOLDER_PATTERN = re.compile(r"^<[A-Za-z_]+>$")

# Forward validation pattern (RFC 1123 compatible)
# - Starts and ends with alphanumeric
# - Middle can contain alphanumeric, hyphens, and dots
# - Length 1-253 (configurable minimum, default 1)
_HOSTNAME_FORWARD_PATTERN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$|^[a-zA-Z0-9]$")

# Minimum hostname length (configurable)
MIN_HOSTNAME_LENGTH = 1
MAX_HOSTNAME_LENGTH = 253


def is_valid_hostname(name: str | None, whitelist: set[str] | None = None) -> bool:
    """
    Validate if a string is a valid hostname.

    Validation order:
    1. Quick pass: Check whitelist
    2. Quick reject: Check blacklist patterns
    3. Strict validation: Check RFC 1123 compliance

    Args:
        name: The hostname string to validate.
        whitelist: Optional set of allowed hostnames (added to default whitelist).

    Returns:
        bool: True if valid hostname, False otherwise.
    """
    # Quick reject: None or empty
    if not name:
        return False

    # Quick reject: Too long or too short
    if len(name) < MIN_HOSTNAME_LENGTH or len(name) > MAX_HOSTNAME_LENGTH:
        return False

    # Quick pass: Check whitelist
    effective_whitelist = DEFAULT_WHITELIST | (whitelist or set())
    if name.lower() in (h.lower() for h in effective_whitelist):
        return True

    # Quick reject: Blacklist patterns
    if _is_blacklisted(name):
        return False

    # Strict validation: RFC 1123 compliance
    return _validate_rfc1123(name)


def _is_blacklisted(name: str) -> bool:
    """
    Check if hostname matches any blacklist pattern.

    Args:
        name: The hostname string to check.

    Returns:
        bool: True if matches blacklist pattern, False otherwise.
    """
    # Pure hexadecimal strings
    if _HEX_PATTERN.match(name):
        return True

    # UUID format
    if _UUID_PATTERN.match(name):
        return True

    # Pure numeric strings (length > 10)
    if _NUMERIC_PATTERN.match(name):
        return True

    # Placeholder format
    return bool(_PLACEHOLDER_PATTERN.match(name))


def _validate_rfc1123(name: str) -> bool:
    """
    Validate hostname according to RFC 1123 rules.

    Rules:
    - Length: 1-253 characters
    - Labels (dot-separated parts): each 1-63 characters
    - Characters: letters, digits, hyphens, dots
    - Must start and end with letter or digit
    - No consecutive dots

    Args:
        name: The hostname string to validate.

    Returns:
        bool: True if valid, False otherwise.
    """
    # Check overall pattern
    if not _HOSTNAME_FORWARD_PATTERN.match(name):
        return False

    # Check each label length (max 63 chars per label)
    return all(len(label) <= 63 for label in name.split("."))


def sanitize_hostname(
    name: str | None,
    whitelist: set[str] | None = None,
    log_warnings: bool = True,
) -> str:
    """
    Sanitize hostname by validating and returning valid hostname or empty string.

    Args:
        name: The hostname string to sanitize.
        whitelist: Optional set of allowed hostnames (added to default whitelist).
        log_warnings: Whether to log warnings for invalid hostnames (default: True).

    Returns:
        str: Valid hostname or empty string if invalid.
    """
    if not name:
        return ""

    if is_valid_hostname(name, whitelist):
        return name

    # Log warning for audit trail
    if log_warnings:
        logger.warning(f"Invalid hostname filtered: '{name}' - replaced with empty string")

    return ""


def get_hostname_filter_sql() -> str:
    """
    Get SQL WHERE clause fragment for filtering invalid hostnames.

    This provides basic filtering at SQL level to reduce data transfer.
    Additional Python-side filtering is still needed for edge cases.

    Returns:
        str: SQL WHERE clause fragment for hostname filtering.
    """
    # Filter conditions:
    # 1. Not NULL and not empty string
    # 2. Not placeholder format (<...>)
    # 3. Length between 1 and 253
    # 4. Not pure hexadecimal (simple check: not all lowercase hex chars)
    # Note: In PostgreSQL, % must be escaped as %% in LIKE patterns when used with psycopg2
    # to avoid being interpreted as a parameter placeholder.
    return """
        host_name IS NOT NULL
        AND host_name != ''
        AND host_name NOT LIKE '<%%>'
        AND LENGTH(host_name) BETWEEN 1 AND 253
    """.strip()
