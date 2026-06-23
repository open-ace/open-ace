"""Workspace base-directory resolution.

Single source of truth for the ``WORKSPACE_BASE_DIR`` env var so the directory
browser's allowed-prefix logic is consistent across routes (fs / admin /
workspace). When the env var is unset, the default is the current user's home
directory (e.g. ``/Users/<user>`` on macOS, ``/home/<user>`` on Linux) so the
browser works out of the box on any platform; Docker/server deployments set
the env explicitly (e.g. ``/workspace``).
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    "get_workspace_base_dir",
    "get_workspace_base_dirs",
    "ensure_system_user",
    "ensure_user_workspace",
]


def get_workspace_base_dir() -> str:
    """Get the workspace base directory. Configurable via WORKSPACE_BASE_DIR env var.

    Falls back to ``str(Path.home())`` when the env var is unset or empty, so
    the directory browser works on macOS (``/Users/<user>``) and Linux
    (``/home/<user>``) alike. Explicit env values — e.g. Docker's ``/workspace``
    — always win.
    """
    return os.environ.get("WORKSPACE_BASE_DIR") or str(Path.home())


def get_workspace_base_dirs() -> list[str]:
    """Get list of workspace base directories. Supports comma-separated WORKSPACE_BASE_DIR.

    Example: ``WORKSPACE_BASE_DIR=/workspace,/tools,/projects``
    Returns: ``['/workspace', '/tools', '/projects']``

    When unset, defaults to ``[str(Path.home())]`` — see
    :func:`get_workspace_base_dir`.
    """
    base_dir = get_workspace_base_dir()
    return [d.strip() for d in base_dir.split(",") if d.strip()]


def _is_docker_multi_user_mode() -> bool:
    """Check if running in Docker multi-user mode.

    Docker multi-user mode is indicated by:
    1. WORKSPACE_BASE_DIR is set (typically /workspace)
    2. Process is running as root (can create system users)

    Returns True if both conditions are met.
    """
    base_dir = os.environ.get("WORKSPACE_BASE_DIR", "")
    # Docker sets WORKSPACE_BASE_DIR=/workspace, Package version uses default (Path.home())
    is_docker_workspace = base_dir == "/workspace"
    # In Docker container, typically running as root
    is_root = os.geteuid() == 0
    return is_docker_workspace and is_root


def ensure_system_user(system_account: str, uid: Optional[int] = None) -> bool:
    """
    Ensure a system user exists for workspace operations.
    Creates the OS user, workspace directory, and .qwen directory.

    Args:
        system_account: Username for the system account.
        uid: Optional specific UID. If None, system auto-assigns.

    Returns:
        True if user exists or was created successfully.
    """
    base_dir = get_workspace_base_dir()

    # Check if user already exists
    result = subprocess.run(["id", system_account], capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"System user {system_account} already exists")
        # Still ensure workspace directories exist
        _ensure_workspace_dirs(system_account, base_dir)
        return True

    # Build useradd command
    cmd = ["useradd", "-m", "-s", "/bin/bash"]
    if uid is not None:
        cmd.extend(["-u", str(uid)])
    cmd.append(system_account)

    logger.info(f"Creating system user: {system_account}" + (f" (UID: {uid})" if uid else ""))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"Failed to create system user {system_account}: {result.stderr}")
        return False

    logger.info(f"System user {system_account} created successfully")
    _ensure_workspace_dirs(system_account, base_dir)
    return True


def _ensure_workspace_dirs(system_account: str, base_dir: str):
    """Ensure workspace directories exist with correct ownership."""
    workspace_dir = f"{base_dir}/{system_account}"
    qwen_dir = f"{workspace_dir}/.qwen"

    for directory in [workspace_dir, qwen_dir]:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, mode=0o755, exist_ok=True)
            except PermissionError:
                logger.warning(f"Cannot create {directory} (permission denied)")
                continue

    # Set ownership
    try:
        uid_result = subprocess.run(["id", "-u", system_account], capture_output=True, text=True)
        gid_result = subprocess.run(["id", "-g", system_account], capture_output=True, text=True)
        if uid_result.returncode == 0 and gid_result.returncode == 0:
            uid = int(uid_result.stdout.strip())
            gid = int(gid_result.stdout.strip())
            for directory in [workspace_dir, qwen_dir]:
                try:
                    os.chown(directory, uid, gid)
                except PermissionError:
                    logger.warning(f"Cannot chown {directory} to {uid}:{gid}")
    except Exception as e:
        logger.warning(f"Error setting ownership for {system_account}: {e}")


def ensure_user_workspace(system_account: str) -> bool:
    """
    Ensure workspace directory exists for user login.
    Called during login to prepare workspace environment.

    Behavior differs by deployment mode:
    - Docker multi-user mode: Creates system user + workspace + .qwen dirs
    - Package single-user mode: Only creates .qwen in user's home

    Args:
        system_account: Username for the system account.

    Returns:
        True if workspace setup succeeded or was already ready.
    """
    if _is_docker_multi_user_mode():
        # Docker multi-user mode: ensure system user and workspace
        logger.info(f"Ensuring workspace for {system_account} in Docker multi-user mode")
        return ensure_system_user(system_account)
    else:
        # Package single-user mode: only create .qwen in home directory
        # system_account may not match actual OS user, use current user's home
        home_dir = str(Path.home())
        qwen_dir = f"{home_dir}/.qwen"

        if not os.path.exists(qwen_dir):
            try:
                os.makedirs(qwen_dir, mode=0o755, exist_ok=True)
                logger.info(f"Created .qwen directory at {qwen_dir}")
            except PermissionError as e:
                logger.warning(f"Cannot create .qwen directory: {e}")
                return False

        return True
