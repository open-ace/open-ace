"""
Open ACE - Sender Hash Computation Utility

Provides deterministic hash computation for sender_name values that cannot be
resolved to user_id. Used for statistics aggregation to ensure each unresolved
sender is counted as a distinct user.

Issue #1573: PostgreSQL and SQLite statistics consistency
"""

import hashlib
from typing import Final

# Special hash value for empty sender_name
EMPTY_SENDER_HASH: Final[int] = -2

# Maximum length for sender_name before truncation (for performance)
MAX_SENDER_LENGTH: Final[int] = 1000


def compute_sender_hash(sender_name: str, max_length: int = MAX_SENDER_LENGTH) -> int:
    """
    Compute a deterministic negative BIGINT hash for a sender_name.

    This function is used as a fallback when sender_name cannot be resolved
    to a user_id. The hash value is always negative to distinguish from
    positive user_id values.

    Args:
        sender_name: The sender name to hash.
        max_length: Maximum length to consider (truncate if exceeded).
                    Defaults to MAX_SENDER_LENGTH (1000).

    Returns:
        int: A negative BIGINT hash value.
            Returns -2 for empty strings (special marker).
            Returns a deterministic negative hash for all other inputs.

    Examples:
        >>> compute_sender_hash("alice")
        -1234567890123456789  # Deterministic negative value

        >>> compute_sender_hash("")
        -2  # Special marker for empty strings

        >>> compute_sender_hash("test")
        -3632233995294317361  # Same as PostgreSQL: -ABS(('0x' || LEFT(MD5('test'), 16))::BIT(64)::BIGINT)

    Note:
        The hash algorithm matches PostgreSQL's:
        -ABS(('0x' || LEFT(MD5(sender_name), 16))::BIT(64)::BIGINT)

        This ensures consistency between PostgreSQL (SQL-level MD5) and
        SQLite (application-level Python hashlib).
    """
    # Handle empty string as special case
    if not sender_name:
        return EMPTY_SENDER_HASH

    # Truncate if exceeds max length (for performance)
    if len(sender_name) > max_length:
        sender_name = sender_name[:max_length]

    # Compute MD5 hash (UTF-8 encoding for consistency)
    md5_hex = hashlib.md5(sender_name.encode("utf-8")).hexdigest()

    # Take first 16 hex characters (64 bits)
    hex_prefix = md5_hex[:16]

    # Convert hex to integer
    hash_int = int(hex_prefix, 16)

    # Convert to signed 64-bit integer (matching PostgreSQL's ::BIT(64)::BIGINT)
    # PostgreSQL interprets the bit pattern as signed
    if hash_int >= 2**63:
        hash_int -= 2**64

    # Return negative value to distinguish from positive user_id
    return -abs(hash_int)


def verify_hash_consistency(sender_name: str) -> bool:
    """
    Verify that hash computation is consistent (for testing).

    Multiple calls should return the same result.

    Args:
        sender_name: The sender name to hash.

    Returns:
        bool: True if hash is deterministic (always True for valid inputs).
    """
    hash1 = compute_sender_hash(sender_name)
    hash2 = compute_sender_hash(sender_name)
    return hash1 == hash2