"""Isolation-aware path resolution (Issue #3420).

This module provides utilities for resolving user home directory paths
based on the isolation level (sandboxed/os_user/none).
"""

import logging

logger = logging.getLogger(__name__)


def get_user_home_for_isolation(system_account: str, isolation_level: str) -> str:
    """Get user home directory path based on isolation level.

    In sandboxed isolation mode, OpenSandbox security policy requires all
    volume mounts to be under /workspace. This function returns the correct
    path based on the user's current isolation level.

    Args:
        system_account: User's system account name.
        isolation_level: Current isolation level (sandboxed/os_user/none).

    Returns:
        User home directory path.

    Examples:
        >>> get_user_home_for_isolation("user1", "sandboxed")
        '/workspace/user1'
        >>> get_user_home_for_isolation("user1", "os_user")
        '/home/user1'  # or WORKSPACE_BASE_DIR/user1
    """
    # Lazy import to avoid circular dependency at module load time
    from app.services.workspace_isolation_contract import ISOLATION_LEVEL_SANDBOXED
    from app.utils.workspace import get_workspace_base_dir

    if isolation_level == ISOLATION_LEVEL_SANDBOXED:
        # sandboxed mode: OpenSandbox requires mounts under /workspace
        return f"/workspace/{system_account}"
    else:
        # os_user or other modes: use WORKSPACE_BASE_DIR
        base_dir = get_workspace_base_dir()
        return f"{base_dir}/{system_account}"