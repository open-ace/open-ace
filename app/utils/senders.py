"""
Open ACE - Sender Validation Utilities

Shared utility for validating sender names.
Used to filter out raw Feishu Open IDs (e.g., "ou_3e479c7f81f8674741d778e8f838f8ed")
that appear when Feishu username resolution fails.

SQL-layer equivalent condition (keep in sync):
    NOT (sender_name LIKE 'ou_%' AND LENGTH(sender_name) > 10)
"""


def is_valid_sender(name: str) -> bool:
    """
    Check if a sender name is a valid display name.

    Returns False for:
    - Empty or None names
    - Feishu Open IDs (start with "ou_" and length > 10)

    Returns True for all other names, including short names starting
    with "ou_" that are 10 characters or less (e.g., "ou_abc").

    Args:
        name: The sender name to validate.

    Returns:
        bool: True if the name is valid for display, False otherwise.
    """
    if not name:
        return False
    # Filter out Feishu user IDs (starts with "ou_" and longer than 10 chars)
    return not (name.startswith("ou_") and len(name) > 10)
