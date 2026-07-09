"""
Open ACE - Sender Validation Utilities

Shared utility for validating sender names.
Used to filter out raw Feishu Open IDs (e.g., "ou_3e479c7f81f8674741d778e8f838f8ed")
that appear when Feishu username resolution fails, as well as placeholder values
like 'null', 'None', 'undefined', 'N/A', 'Unknown', etc.

SQL-layer equivalent condition (keep in sync with get_sender_filter_sql()):
    NOT (sender_name LIKE 'ou_%%' AND LENGTH(sender_name) > 10)
    AND sender_name NOT IN ('null', 'None', 'undefined', 'N/A', 'Unknown', 'unknown')
    AND sender_name NOT LIKE '<%%>'
"""

# Invalid sender patterns - placeholder values that should be filtered out
_INVALID_SENDER_PATTERNS = frozenset(
    [
        "null",
        "None",
        "undefined",
        "N/A",
        "Unknown",
        "unknown",
    ]
)


def get_sender_filter_sql(column_name: str = "sender_name") -> str:
    """
    Get SQL WHERE clause fragment for filtering invalid sender names.

    This provides basic filtering at SQL level to reduce data transfer.
    Additional Python-side filtering via is_valid_sender() is still needed
    for edge cases.

    **Security Note**: column_name is directly interpolated into SQL.
    Callers MUST ensure column_name is a trusted identifier (e.g., from
    code constants, NOT from user input).

    Args:
        column_name: The column name to filter (default: "sender_name").

    Returns:
        str: SQL WHERE clause fragment for sender name filtering.
    """
    # Build placeholder values IN clause
    placeholder_values = "', '".join(sorted(_INVALID_SENDER_PATTERNS))

    # Use %% for LIKE patterns to work with both PostgreSQL (psycopg2) and SQLite:
    # - PostgreSQL: %% is escaped to % by psycopg2 (which uses %s as placeholder)
    # - SQLite: %% is interpreted as literal % (safe since SQLite uses ? placeholder)
    return (
        f"NOT ({column_name} LIKE 'ou_%%' AND LENGTH({column_name}) > 10) "
        f"AND {column_name} NOT IN ('{placeholder_values}') "
        f"AND {column_name} NOT LIKE '<%%>'"
    )


def is_valid_sender(name: str) -> bool:
    """
    Check if a sender name is a valid display name.

    Returns False for:
    - Empty or None names
    - Placeholder values (null, None, undefined, N/A, Unknown, etc.)
    - Feishu Open IDs (start with "ou_" and length > 10)
    - Placeholder format <...> (e.g., <unknown>, <None>)

    Returns True for all other names, including short names starting
    with "ou_" that are 10 characters or less (e.g., "ou_abc").

    Args:
        name: The sender name to validate.

    Returns:
        bool: True if the name is valid for display, False otherwise.
    """
    if not name:
        return False

    # Filter out placeholder values
    if name in _INVALID_SENDER_PATTERNS:
        return False

    # Filter out placeholder format <...>
    if name.startswith("<") and name.endswith(">"):
        return False

    # Filter out Feishu user IDs (starts with "ou_" and longer than 10 chars)
    return not (name.startswith("ou_") and len(name) > 10)
