"""
Open ACE - Role Groups Configuration

Defines role group mappings for user distribution statistics.
Issue #3079: Support role-based user grouping in trend analysis.

Role groups consolidate granular roles into display-friendly categories:
- admin: System administrators (admin, platform_admin, tenant_admin)
- manager: Team managers (manager)
- user: Regular users (user, readonly)
- unknown: Unassigned or unrecognized roles
"""

from typing import Literal

# Role group definitions
# Maps display group names to the set of underlying roles they represent
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
