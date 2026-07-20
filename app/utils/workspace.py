"""Workspace base-directory resolution.

Single source of truth for the ``WORKSPACE_BASE_DIR`` env var so the directory
browser's allowed-prefix logic is consistent across routes (fs / admin /
workspace). When the env var is unset, the default is the current user's home
directory (e.g. ``/Users/<user>`` on macOS, ``/home/<user>`` on Linux) so the
browser works out of the box on any platform; Docker/server deployments set
the env explicitly (e.g. ``/workspace``).
"""

from __future__ import annotations
import logging
import os
import platform
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "get_workspace_base_dir",
    "get_workspace_base_dirs",
    "run_as_root_if_needed",
    "ensure_system_user",
    "ensure_user_workspace",
]

# Wrapper script paths (Issue #1855)
OPENACE_USERADD_WRAPPER = "/usr/local/bin/openace-useradd"
OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
OPENACE_MKDIR_WRAPPER = "/usr/local/bin/openace-mkdir"
# Cross-user file write wrapper (Issue #1916): used by the upload endpoint in
# Package non-root multi-user mode to write into a user's 0700 home directory.
# cp/tee/mv are NOT in the sudoers OPENACE_UTILS whitelist, so uploads delegate
# through this root-authorized wrapper (which drops to the target user via
# runuser). Docker multi-user runs as root and never hits this path.
OPENACE_WRITE_AS_WRAPPER = "/usr/local/bin/openace-write-as"


def _is_wrapper_available(wrapper_path: str) -> bool:
    """Check if a security wrapper script is available and executable."""
    return os.path.isfile(wrapper_path) and os.access(wrapper_path, os.X_OK)


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


def run_as_root_if_needed(cmd: list) -> subprocess.CompletedProcess:
    """以 root 权限执行命令（用于 useradd/chown/mkdir 等系统管理操作）。

    当服务以非 root 用户运行时（如 Package 版 ivyent），需要通过 sudo 执行
    需要 root 权限的系统命令。

    注意：此函数仅用于需要 root 权限的命令（useradd, chown, mkdir）。
    id 命令不应使用此函数，因为 id 命令任何用户都可以执行。

    Args:
        cmd: 命令列表，如 ["useradd", "-m", "-s", "/bin/bash", "username"]

    Returns:
        subprocess.CompletedProcess 结果。
    """
    if os.geteuid() != 0:
        return subprocess.run(["sudo"] + cmd, capture_output=True, text=True)
    return subprocess.run(cmd, capture_output=True, text=True)


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


def ensure_system_user(system_account: str, uid: int | None = None) -> bool:
    """确保系统用户存在，创建工作目录。

    此函数用于 Package 版 multi-user mode，当服务以非 root 用户运行时，
    通过 sudo 执行 useradd 和 chown 命令。

    Args:
        system_account: 用户名（必须符合 Linux useradd 要求）
        uid: 可选 UID，必须 >= 1000（系统保留 UID < 1000）

    Returns:
        True 如果用户存在或创建成功。
    """
    # 用户名格式验证（Linux useradd 要求）
    # - Must start with a lowercase letter or underscore
    # - Can contain lowercase letters, digits, underscores, and dashes
    # - Maximum 32 characters
    # - No spaces or special characters
    if not system_account:
        logger.error("Empty username provided")
        return False

    if len(system_account) > 32:
        logger.error(f"Username too long (max 32 chars): {system_account}")
        return False

    # Linux username pattern: [a-z_][a-z0-9_-]*
    if not re.match(r"^[a-z_][a-z0-9_-]*$", system_account):
        logger.error(f"Invalid username format: {system_account}")
        return False

    # macOS 特殊处理（无 useradd）
    if platform.system() == "Darwin":
        logger.debug(f"Skipping system user creation on macOS for: {system_account}")
        return True

    # uid 安全验证：禁止创建系统保留 UID (< 1000)
    if uid is not None and uid < 1000:
        logger.error(f"UID {uid} is reserved for system users, rejected")
        return False

    base_dir = get_workspace_base_dir()

    # 检查用户是否存在（id 命令不需要 sudo，任何用户都可以执行）
    result = subprocess.run(["id", system_account], capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"System user {system_account} already exists")
        # Still ensure workspace directories exist
        _ensure_workspace_dirs(system_account, base_dir)
        return True

    # 创建用户（通过 wrapper 或 sudo）
    # Issue #1855: 优先使用安全 wrapper，wrapper 内部做参数校验和审计日志
    if _is_wrapper_available(OPENACE_USERADD_WRAPPER):
        cmd = [OPENACE_USERADD_WRAPPER, system_account]
        if uid is not None:
            cmd.extend(["-u", str(uid)])
        logger.info(
            f"Creating system user via wrapper: {system_account}"
            + (f" (UID: {uid})" if uid else "")
        )
        result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        # Fallback: 使用传统 useradd 命令（需要 sudo）
        cmd = ["useradd", "-m", "-s", "/bin/bash"]
        if uid is not None:
            cmd.extend(["-u", str(uid)])
        cmd.append(system_account)
        logger.info(f"Creating system user: {system_account}" + (f" (UID: {uid})" if uid else ""))
        result = run_as_root_if_needed(cmd)

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

    # 创建目录（必要时通过 wrapper 或 sudo）
    for directory in [workspace_dir, qwen_dir]:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, mode=0o755, exist_ok=True)
            except PermissionError:
                # Issue #1855: 优先使用安全 wrapper
                if _is_wrapper_available(OPENACE_MKDIR_WRAPPER):
                    result = subprocess.run(
                        [OPENACE_MKDIR_WRAPPER, system_account, directory],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        logger.warning(f"Cannot create {directory} via wrapper: {result.stderr}")
                        continue
                else:
                    # Fallback: 使用传统 mkdir 命令
                    result = run_as_root_if_needed(["mkdir", "-p", "-m", "755", directory])
                    if result.returncode != 0:
                        logger.warning(f"Cannot create {directory}: {result.stderr}")
                        continue

    # 获取 UID/GID（id 命令不需要 sudo，任何用户都可以执行）
    uid_result = subprocess.run(["id", "-u", system_account], capture_output=True, text=True)
    gid_result = subprocess.run(["id", "-g", system_account], capture_output=True, text=True)

    if uid_result.returncode == 0 and gid_result.returncode == 0:
        uid = int(uid_result.stdout.strip())
        gid = int(gid_result.stdout.strip())

        # 设置所有权（通过 wrapper 或 sudo）
        # Issue #1855: 优先使用安全 wrapper，wrapper 内部做路径校验和审计日志
        for directory in [workspace_dir, qwen_dir]:
            if _is_wrapper_available(OPENACE_CHOWN_WRAPPER):
                result = subprocess.run(
                    [OPENACE_CHOWN_WRAPPER, f"{uid}:{gid}", directory],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.warning(f"Cannot chown {directory} via wrapper: {result.stderr}")
            else:
                # Fallback: 使用传统 chown 命令
                result = run_as_root_if_needed(["chown", f"{uid}:{gid}", directory])
                if result.returncode != 0:
                    logger.warning(f"Cannot chown {directory} to {uid}:{gid}: {result.stderr}")


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
