"""
Open ACE - Role Groups Configuration

Defines role group mappings for user distribution statistics.
Issue #3079: Support role-based user grouping in trend analysis.

Role groups consolidate granular roles into display-friendly categories:
- admin: System administrators (admin, platform_admin, tenant_admin)
- manager: Team managers (manager)
- user: Regular users (user, readonly)
- unknown: Unassigned or unrecognized roles

Note on SQL vs Python usage:
SQL queries (in message_repo.py and daily_stats_repo.py) use CASE WHEN expressions
for role grouping because:
1. It allows the database to aggregate data efficiently in a single query
2. It avoids the need for N+1 queries (fetching users then calling Python function)
3. The MAX(CASE WHEN ... END) pattern correctly aggregates roles per user

The `normalize_role_to_group` function is provided for Python-side processing
when needed (e.g., validation, testing, or future use cases where SQL aggregation
is not involved).
"""

from typing import Literal

# Role group definitions
# Maps display group names to the set of underlying roles they represent
# Note: Keep in sync with CASE WHEN expressions in SQL queries
ROLE_GROUPS: dict[str, frozenset[str]] = {
    "admin": frozenset({"admin", "platform_admin", "tenant_admin"}),
    "manager": frozenset({"manager"}),
    "user": frozenset({"user", "readonly"}),
}

# Type for valid role group names
RoleGroup = Literal["admin", "manager", "user", "unknown"]


def normalize_role_to_group(role: str | None) -> str:
    """
    Normalize a specific role to its display group.

    This function mirrors the CASE WHEN logic used in SQL queries.
    Keep it in sync with the SQL expressions in message_repo.py and daily_stats_repo.py.

    Args:
        role: The specific role string (e.g., 'admin', 'platform_admin', 'user').
              None or empty string will return 'unknown'.

    Returns:
        str: The display group name ('admin', 'manager', 'user', or 'unknown').

    Examples:
        >>> normalize_role_to_group('admin')
        'admin'
        >>> normalize_role_to_group('platform_admin')
        'admin'
        >>> normalize_role_to_group('user')
        'user'
        >>> normalize_role_to_group(None)
        'unknown'
        >>> normalize_role_to_group('invalid')
        'unknown'
    """
    if not role:
        return "unknown"

    for group, roles in ROLE_GROUPS.items():
        if role in roles:
            return group

    return "unknown"
