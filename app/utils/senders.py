"""
Open ACE - Sender Validation Utilities

Shared utility for validating sender names.
Used to filter out raw Feishu Open IDs (e.g., "ou_3e479c7f81f8674741d778e8f838f8ed")
that appear when Feishu username resolution fails.

SQL-layer equivalent condition (keep in sync):
    NOT (sender_name LIKE 'ou_%' AND LENGTH(sender_name) > 10)
"""

# Invalid sender patterns as (prefix, length_threshold) tuples.
# A sender name matching any pattern (starts with prefix AND length > threshold)
# is considered an invalid system ID.
# Extend this list for future system ID formats.
_INVALID_SENDER_PATTERNS: list[tuple[str, int]] = [
    # Feishu Open IDs: "ou_" prefix + long hex string
    ("ou_", 10),
]


def is_valid_sender(name: str) -> bool:
    """
    Check if a sender name is a valid display name.

    Returns False for:
    - Empty or None names
    - System-generated IDs matching patterns in _INVALID_SENDER_PATTERNS
      (e.g., Feishu Open IDs starting with "ou_" and longer than 10 chars)

    Returns True for all other names, including short names starting
    with "ou_" that are 10 characters or less (e.g., "ou_abc").

    Args:
        name: The sender name to validate.

    Returns:
        bool: True if the name is valid for display, False otherwise.
    """
    if not name:
        return False
    for prefix, length_threshold in _INVALID_SENDER_PATTERNS:
        if name.startswith(prefix) and len(name) > length_threshold:
            return False
    return True
