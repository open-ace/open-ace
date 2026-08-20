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
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "get_workspace_base_dir",
    "get_workspace_base_dirs",
    "run_as_root_if_needed",
    "ensure_system_user",
    "ensure_user_workspace",
    "SHARED_GROUP_NAME",
    "ensure_shared_group",
    "add_user_to_shared_group",
    "setup_shared_project_permissions",
    "estimate_file_count_fast",
    "setup_permissions_with_depth_limit",
    "verify_setgid_support",
]

# Shared project group name (Issue #2730)
SHARED_GROUP_NAME = "openace-shared"

# Wrapper script paths (Issue #1855 + #2181)
OPENACE_USERADD_WRAPPER = "/usr/local/bin/openace-useradd"
OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
OPENACE_CAT_WRAPPER = "/usr/local/bin/openace-cat"
OPENACE_MKDIR_WRAPPER = "/usr/local/bin/openace-mkdir"
# Secure rm wrapper (Issue #2181): validates path, user, owner, and dangerous options
OPENACE_RM_WRAPPER = "/usr/local/bin/openace-rm"
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
        return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, cwd="/tmp")
    return subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp")


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

        # Issue #2730: Ensure user is in shared group for shared project access
        if _is_docker_multi_user_mode():
            if not add_user_to_shared_group(system_account):
                logger.warning(f"Failed to add {system_account} to shared group")

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

    # Issue #2730: Add user to shared group for shared project access
    if _is_docker_multi_user_mode():
        if not add_user_to_shared_group(system_account):
            logger.warning(f"Failed to add {system_account} to shared group")

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


# ============================================================================
# Shared Project Permission Management (Issue #2730)
# ============================================================================


def ensure_shared_group() -> bool:
    """Ensure the shared project group exists.

    Creates the 'openace-shared' group if it doesn't exist.
    Uses 'groupadd -f' to be idempotent and avoid race conditions.

    Returns:
        True if group exists or was created successfully.
    """
    if not _is_docker_multi_user_mode():
        return True  # Skip in non-Docker mode

    result = subprocess.run(
        ["groupadd", "-f", SHARED_GROUP_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"Failed to create shared group: {result.stderr}")
        return False

    logger.info(f"Shared group '{SHARED_GROUP_NAME}' ensured")
    return True


def add_user_to_shared_group(system_account: str) -> bool:
    """Add a user to the shared project group.

    Uses 'usermod -aG' which is idempotent (safe to call multiple times).

    Args:
        system_account: Username to add to the shared group.

    Returns:
        True if user was added or already in group.
    """
    if not _is_docker_multi_user_mode():
        return True  # Skip in non-Docker mode

    result = subprocess.run(
        ["usermod", "-aG", SHARED_GROUP_NAME, system_account],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(f"Failed to add {system_account} to shared group: {result.stderr}")
        return False

    logger.info(f"User '{system_account}' added to shared group")
    return True


def setup_shared_project_permissions(path: str) -> tuple[bool, str]:
    """Set up shared project directory permissions.

    Configures a directory for shared project access:
    1. Ensures shared group exists
    2. Sets group ownership to openace-shared
    3. Sets permissions to 2775 (setgid + group rwx)
    4. Recursively fixes existing subdirectories and files

    Args:
        path: Absolute path to the project directory.

    Returns:
        Tuple of (success, error_message). error_message is empty on success.
    """
    if not _is_docker_multi_user_mode():
        return (True, "")  # Skip in non-Docker mode

    if not path:
        return (False, "Path is required")

    if not os.path.isabs(path):
        return (False, f"Path must be absolute: {path}")

    if not os.path.exists(path):
        return (False, f"Path does not exist: {path}")

    if not os.path.isdir(path):
        return (False, f"Path is not a directory: {path}")

    try:
        # 1. Ensure shared group exists
        if not ensure_shared_group():
            return (False, "Failed to create shared group")

        # 2. Set group ownership
        result = subprocess.run(
            ["chown", f":{SHARED_GROUP_NAME}", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"chown failed: {result.stderr}")

        # 3. Set permissions (setgid + 2775)
        result = subprocess.run(
            ["chmod", "2775", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"chmod failed: {result.stderr}")

        # 4. Recursively fix existing subdirectories and files
        # Use find to set permissions on existing content
        subprocess.run(
            ["find", path, "-type", "d", "-exec", "chmod", "2775", "{}", ";"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        subprocess.run(
            ["find", path, "-type", "f", "-exec", "chmod", "664", "{}", ";"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        logger.info(f"Shared project permissions set for: {path}")
        return (True, "")

    except subprocess.TimeoutExpired:
        return (False, "Permission setup timed out")
    except Exception as e:
        return (False, str(e))


# ============================================================================
# Performance Optimization Functions (Issue #2746)
# ============================================================================


def estimate_file_count_fast(path: str, timeout: int = 5) -> int:
    """Fast file count estimation using sampling method.

    Samples only the first 3 directory levels and extrapolates total.
    This avoids traversing the entire directory tree for large projects.

    Args:
        path: Absolute path to the project directory.
        timeout: Maximum time (seconds) for estimation.

    Returns:
        Estimated total file count. Returns 50000 if estimation times out.
    """
    import time

    if not os.path.isabs(path) or not os.path.exists(path):
        return 50000  # Default to maximum

    try:
        start_time = time.time()

        # Count files in first 3 levels only
        result = subprocess.run(
            ["find", path, "-maxdepth", "3", "-type", "f"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            return 50000

        lines = result.stdout.strip().split("\n")
        count_3_levels = len([l for l in lines if l])  # Count non-empty lines

        # Extrapolate: assume each level doubles (typical tree structure)
        # For 10 levels: total ≈ count_3_levels * 2^(10-3) = count_3_levels * 128
        # Cap at reasonable maximum
        estimated_total = min(count_3_levels * 128, 50000)

        elapsed = time.time() - start_time
        logger.debug(
            f"Estimated {estimated_total} files from {count_3_levels} samples "
            f"(took {elapsed:.2f}s)"
        )

        return estimated_total

    except subprocess.TimeoutExpired:
        logger.warning(f"File count estimation timed out after {timeout}s")
        return 50000
    except Exception as e:
        logger.error(f"Error estimating file count: {e}")
        return 50000


def setup_permissions_with_depth_limit(
    path: str,
    depth_limit: int | None = None,
    timeout: int = 60,
    progress_callback=None,
    user_id: int | None = None,
    project_id: int | None = None,
) -> tuple[bool, str, int]:
    """Set permissions with optional recursion depth limit.

    Optimized version that:
    1. Uses batch processing (find | xargs) instead of -exec
    2. Limits recursion depth when specified
    3. Provides progress feedback via callback
    4. Has better timeout handling
    5. Records audit log for permission setup (Issue #2745)

    Args:
        path: Absolute path to the project directory.
        depth_limit: Maximum recursion depth (None = no limit).
        timeout: Timeout in seconds for entire operation.
        progress_callback: Optional callback function(percent, processed, total).
        user_id: User ID who initiated the operation (for audit log).
        project_id: Project ID for audit log resource_id.

    Returns:
        Tuple of (success, error_message, files_processed).
    """
    import time

    if not _is_docker_multi_user_mode():
        return (True, "", 0)

    if not path or not os.path.isabs(path) or not os.path.exists(path):
        return (False, "Invalid path", 0)

    operation_start_time = time.time()
    operation_start_datetime = datetime.now().isoformat()

    try:
        # Ensure shared group
        if not ensure_shared_group():
            return (False, "Failed to create shared group", 0)

        # Set root directory permissions
        subprocess.run(
            ["chown", f":{SHARED_GROUP_NAME}", path],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["chmod", "2775", path],
            capture_output=True,
            text=True,
            check=True,
        )

        # Build find command with optional depth limit
        find_cmd = ["find", path]
        if depth_limit:
            find_cmd.extend(["-maxdepth", str(depth_limit)])

        files_processed = 0

        # Process directories with batch chmod (more efficient than -exec)
        dir_cmd = find_cmd + ["-type", "d"]
        dir_result = subprocess.run(
            dir_cmd,
            capture_output=True,
            text=True,
            timeout=timeout // 2,
        )

        if dir_result.returncode == 0 and dir_result.stdout.strip():
            dirs = [d for d in dir_result.stdout.strip().split("\n") if d]
            # Batch process: use xargs to run chmod on multiple dirs at once
            chmod_process = subprocess.Popen(
                ["xargs", "-0", "chmod", "2775"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Use null-separated input for xargs
            chmod_process.communicate(input="\0".join(dirs), timeout=timeout // 2)
            files_processed += len(dirs)

            if progress_callback and len(dirs) > 100:
                progress_callback(50, files_processed, -1)

        # Process files with batch chmod
        file_cmd = find_cmd + ["-type", "f"]
        file_result = subprocess.run(
            file_cmd,
            capture_output=True,
            text=True,
            timeout=timeout // 2,
        )

        if file_result.returncode == 0 and file_result.stdout.strip():
            files = [f for f in file_result.stdout.strip().split("\n") if f]
            # Batch process files
            chmod_process = subprocess.Popen(
                ["xargs", "-0", "chmod", "664"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            chmod_process.communicate(input="\0".join(files), timeout=timeout // 2)
            files_processed += len(files)

            if progress_callback:
                progress_callback(100, files_processed, files_processed)

        logger.info(f"Set permissions for {files_processed} items in {path}")

        # Record audit log for successful permission setup (Issue #2745)
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=True,
            files_processed=files_processed,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            depth_limit=depth_limit,
        )

        return (True, "", files_processed)

    except subprocess.TimeoutExpired:
        error_msg = f"Operation timed out after {timeout}s"
        # Record audit log for failed permission setup (Issue #2745)
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)
    except subprocess.CalledProcessError as e:
        error_msg = f"Command failed: {e.stderr}"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        _log_permission_audit(
            user_id=user_id,
            project_id=project_id,
            path=path,
            success=False,
            files_processed=0,
            operation_start_time=operation_start_time,
            operation_start_datetime=operation_start_datetime,
            error_message=error_msg,
            depth_limit=depth_limit,
        )
        return (False, error_msg, 0)


def verify_setgid_support(path: str) -> tuple[bool, str]:
    """Verify that setgid is supported and working on the filesystem.

    Creates a test subdirectory to verify that:
    1. setgid bit can be set on directories (2775)
    2. New subdirectories inherit the setgid bit

    Args:
        path: Path to test (will create a temporary subdirectory).

    Returns:
        Tuple of (supported, error_message).
    """
    import tempfile

    if not os.path.isabs(path) or not os.path.exists(path):
        return (False, "Path does not exist")

    test_dir = None
    try:
        # Create temporary test directory
        test_dir = tempfile.mkdtemp(prefix=".setgid_test_", dir=path)

        # Set setgid on test directory
        result = subprocess.run(
            ["chmod", "2775", test_dir],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, f"Failed to set setgid: {result.stderr}")

        # Create subdirectory to test inheritance
        subdir = os.path.join(test_dir, "test_subdir")
        os.makedirs(subdir)

        # Check if subdirectory inherited setgid
        stat_result = os.stat(subdir)
        mode = stat_result.st_mode

        # setgid bit is 0o2000 (octal)
        has_setgid = bool(mode & 0o2000)

        if has_setgid:
            logger.info(f"setgid inheritance verified at {path}")
            return (True, "")
        else:
            logger.warning(f"setgid not inherited at {path}")
            return (False, "setgid not inherited by new subdirectories")

    except Exception as e:
        return (False, f"Verification failed: {e}")
    finally:
        # Clean up test directory
        if test_dir and os.path.exists(test_dir):
            import shutil

            shutil.rmtree(test_dir, ignore_errors=True)


# ============================================================================
# Audit Log Helper Functions (Issue #2745)
# ============================================================================


def _log_permission_audit(
    user_id: int | None,
    project_id: int | None,
    path: str,
    success: bool,
    files_processed: int,
    operation_start_time: float,
    operation_start_datetime: str,
    error_message: str | None = None,
    depth_limit: int | None = None,
) -> None:
    """Record audit log for shared project permission setup.

    Uses a separate database connection to avoid transaction rollback issues.
    Failures are logged but never raise exceptions.

    Args:
        user_id: User ID who initiated the operation.
        project_id: Project ID for resource_id.
        path: Project path.
        success: Whether the operation succeeded.
        files_processed: Number of files processed.
        operation_start_time: Unix timestamp when operation started.
        operation_start_datetime: ISO format datetime when operation started.
        error_message: Error message if operation failed.
        depth_limit: Recursion depth limit used.
    """
    import time

    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()

        operation_end_time = time.time()
        duration_seconds = operation_end_time - operation_start_time

        details = {
            "path": path,
            "files_processed": files_processed,
            "operation_start_time": operation_start_datetime,
            "operation_end_time": datetime.now().isoformat(),
            "duration_seconds": round(duration_seconds, 2),
            "success": success,
        }

        if depth_limit is not None:
            details["depth_limit"] = depth_limit

        if error_message:
            details["error_message"] = error_message

        audit_logger.log_action(
            action=AuditAction.SHARED_PROJECT_PERMISSION_SETUP_COMPLETE,
            user_id=user_id,
            resource_type="project",
            resource_id=str(project_id) if project_id else None,
            details=details,
            success=success,
            error_message=error_message,
        )

        logger.debug(
            f"Recorded permission audit log: user_id={user_id}, project_id={project_id}, "
            f"path={path}, success={success}, files_processed={files_processed}"
        )

    except Exception as e:
        # Audit log failure should not affect main operation
        logger.error(f"Failed to record permission audit log: {e}")
