from __future__ import annotations

"""
Open ACE - File System Routes

API routes for file system browsing operations.
Used by project selector UI to browse directories.
"""

import logging
import os
import platform
import pwd
import subprocess
from pathlib import Path
from typing import Any

from flask import Blueprint, g, jsonify, request

from app.repositories.user_repo import UserRepository
from app.utils.workspace import get_workspace_base_dir, get_workspace_base_dirs

logger = logging.getLogger(__name__)

fs_bp = Blueprint("fs", __name__)
user_repo = UserRepository()

# System-sensitive directories blacklist (Linux/Mac)
# These directories should never be writable by users to prevent system damage
BLACKLISTED_PATHS = [
    "/etc",  # System configuration
    "/bin",  # Binary executables
    "/sbin",  # System binaries
    "/usr",  # All user system files (covers /usr/bin, /usr/sbin, /usr/lib, etc.)
    "/usr/local",  # User-installed software
    "/usr/share",  # Shared data files
    "/root",  # Root user home
    "/boot",  # Boot files
    "/dev",  # Device files
    "/proc",  # Process information
    "/sys",  # System information
    "/var",  # System variable data (covers /var/log, /var/lib, etc.)
    "/opt",  # Optional software packages
    "/tmp",  # Temporary files (security risk for arbitrary creation)
    "/lib",  # Shared libraries
    "/lib64",  # 64-bit libraries
]

# Resolved blacklist used for matching: each literal is canonicalized through
# realpath so symlinked entries still match. On macOS /etc → /private/etc,
# /var → /private/var, /tmp → /private/tmp; without this, a path like /etc
# (realpath /private/etc) would slip past the literal /etc check. Keep both the
# literal (for readability/docs above) and its realpath here.
_BLACKLISTED_RESOLVED = {
    *BLACKLISTED_PATHS,
    *(os.path.realpath(p) for p in BLACKLISTED_PATHS),
}


@fs_bp.before_request
def _authenticate_user():
    """Authenticate via session token or WebUI token (for iframe integration)."""
    # Skip auth for OPTIONS preflight requests
    if request.method == "OPTIONS":
        return None

    # Try session token first
    from app.auth.decorators import (
        _extract_token,
        _load_user_from_token,
        enforce_password_change_requirement,
    )

    token = _extract_token()
    if token:
        user_data = _load_user_from_token(token)
        if user_data:
            user = user_repo.get_user_by_id(int(user_data.get("id", 0)))
            if user:
                g.user = user
                g.user_id = user.get("id")
                g.user_role = user.get("role")
                password_change_response = enforce_password_change_requirement(user)
                if password_change_response is not None:
                    return password_change_response
                return None

    # Fallback: try WebUI token from query param (for iframe integration)
    url_token = request.args.get("token")
    if url_token:
        from app.services.webui_manager import get_webui_manager

        manager = get_webui_manager()
        if manager:
            valid, user_id, error = manager.validate_token(url_token)
            if valid and user_id:
                user = user_repo.get_user_by_id(user_id)
                if user:
                    g.user = user
                    g.user_id = user_id
                    g.user_role = user.get("role")
                    password_change_response = enforce_password_change_requirement(user)
                    if password_change_response is not None:
                        return password_change_response
                    return None

    return jsonify({"error": "Authentication required"}), 401


def get_effective_system_account(system_account: str | None) -> str | None:
    """Check if current user is already the target user.

    When NoNewPrivileges=true is set in systemd, sudo is blocked.
    If the process is already running as the target user, we can skip sudo.

    Returns None if current user matches target user, otherwise returns system_account.
    """
    if not system_account:
        return None

    current_user = pwd.getpwuid(os.getuid()).pw_name
    if current_user == system_account:
        return None

    return system_account


def run_as_user(system_account: str, command: list) -> subprocess.CompletedProcess:
    """Run a command as a specific user using sudo."""
    sudo_cmd = ["sudo", "-u", system_account] + command
    return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=10)


def get_current_user():
    """Get current user from session token."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not token:
        return None, {"error": "Unauthorized"}, 401

    from app.auth.decorators import _load_user_from_token

    user_data = _load_user_from_token(token)
    if not user_data:
        return None, {"error": "Unauthorized"}, 401
    user = user_repo.get_user_by_id(int(user_data.get("id", 0)))
    return user, None, 200


def get_webui_user():
    """Get user from webui token (for iframe integration)."""
    from app.services.webui_manager import get_webui_manager

    token: str | None = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not token:
        token = request.args.get("token") or ""

    if not token:
        return None, {"error": "Unauthorized"}, 401

    manager = get_webui_manager()
    if not manager:
        return None, {"error": "WebUI manager not available"}, 500

    valid, user_id, error = manager.validate_token(token)
    if not valid or user_id is None:
        return None, {"error": error}, 401

    user = user_repo.get_user_by_id(user_id)
    return user, None, 200


def get_home_directory(user=None):
    """Get user's home directory based on system_account."""
    base_dir = get_workspace_base_dir()
    if user:
        system_account = user.get("system_account") or user.get("username")
        effective_system_account = get_effective_system_account(system_account)
        user_home = f"{base_dir}/{system_account}"
        if effective_system_account:
            # Return the system account's workspace directory
            # Use sudo to check if directory exists
            result = run_as_user(effective_system_account, ["test", "-e", user_home])
            if result.returncode == 0:
                return user_home
            # Directory doesn't exist yet - return expected path
            # Frontend will show "directory doesn't exist" hint
            logger.info(
                f"Workspace directory {user_home} doesn't exist yet for user {system_account}"
            )
            return user_home
        elif system_account:
            # Already running as target user, check directly
            if os.path.exists(user_home):
                return user_home
            # Directory doesn't exist yet - return expected path
            logger.info(
                f"Workspace directory {user_home} doesn't exist yet for user {system_account}"
            )
            return user_home
    # Fallback to process user's home
    return str(Path.home())


def is_valid_path(path: str, allowed_prefixes: list[str] | None = None) -> bool:
    """Check if path is valid for browsing.

    Optionally restricts the resolved path to a list of allowed prefix
    directories (e.g. workspace base dir). If allowed_prefixes is None,
    no prefix restriction is applied (backward compatible).

    Also checks against system-sensitive directory blacklist to prevent
    users from writing to /etc, /bin, /root, etc.
    """
    if not path:
        return False

    # Check for path traversal in the original input
    if ".." in path:
        return False

    # Platform-specific validation for original path
    system = platform.system()
    if system == "Windows":
        # Windows: must be a valid drive path
        if not (len(path) >= 2 and path[1] == ":"):
            return False
    else:
        # Mac/Linux: must start with / (absolute path required)
        if not path.startswith("/"):
            return False

    # Resolve to absolute path, following symlinks to detect traversal
    try:
        abs_path = os.path.realpath(path)
    except Exception:
        return False

    # Blacklist check for Linux/Mac - protect system directories
    if system != "Windows":
        for blocked in _BLACKLISTED_RESOLVED:
            if abs_path == blocked or abs_path.startswith(blocked + os.sep):
                return False

    # Restrict resolved path to allowed prefixes if provided.
    # Ensure path-separator boundary to prevent /home/user_evil matching /home.
    if allowed_prefixes:
        if not any(
            abs_path == prefix or abs_path.startswith(prefix + os.sep)
            for prefix in allowed_prefixes
        ):
            return False

    return True


def get_directory_info(path: str, system_account: str | None = None):
    """Get information about a directory, optionally as a specific user."""
    try:
        # Check if current process user is already the target user
        # This avoids sudo failures when NoNewPrivileges=true is set
        effective_system_account = get_effective_system_account(system_account)

        if effective_system_account:
            # Use sudo to check permissions as the specified user
            # Check if directory exists
            result = run_as_user(effective_system_account, ["test", "-e", path])
            if result.returncode != 0:
                return {
                    "exists": False,
                    "is_dir": False,
                    "is_readable": False,
                    "is_writable": False,
                }

            # Check if it's a directory
            result = run_as_user(effective_system_account, ["test", "-d", path])
            is_dir = result.returncode == 0

            if not is_dir:
                return {
                    "exists": True,
                    "is_dir": False,
                    "is_readable": False,
                    "is_writable": False,
                }

            # Check if readable
            result = run_as_user(effective_system_account, ["test", "-r", path])
            is_readable = result.returncode == 0

            # Check if writable
            result = run_as_user(effective_system_account, ["test", "-w", path])
            is_writable = result.returncode == 0

            return {
                "exists": True,
                "is_dir": is_dir,
                "is_readable": is_readable,
                "is_writable": is_writable,
                "size": 0,
            }
        else:
            # Direct permission checks (process user or already target user)
            stat = os.stat(path)
            return {
                "exists": True,
                "is_dir": os.path.isdir(path),
                "is_readable": os.access(path, os.R_OK),
                "is_writable": os.access(path, os.W_OK),
                "size": stat.st_size if os.path.isfile(path) else 0,
            }
    except Exception as e:
        logger.debug(f"Error getting directory info for {path}: {e}")
        return {
            "exists": False,
            "is_dir": False,
            "is_readable": False,
            "is_writable": False,
            "error": "Internal server error",
        }


@fs_bp.route("/fs/browse", methods=["GET"])
def api_browse_directory():
    """Browse a directory and list subdirectories."""
    user = g.user

    # Get system_account for sudo operations
    system_account = user.get("system_account") if user else None

    # Get path parameter
    path = request.args.get("path", "")

    # Handle special path values
    if not path or path.lower() == "home":
        path = get_home_directory(user)
    else:
        # Validate and resolve path — restrict to workspace base dirs
        base_dirs = get_workspace_base_dirs()
        if not is_valid_path(path, allowed_prefixes=base_dirs):
            allowed_paths = ", ".join(base_dirs)
            return jsonify({"error": f"Path must be under one of: {allowed_paths}"}), 400

        path = os.path.realpath(path)

    # Check if path exists and is readable
    dir_info = get_directory_info(path, system_account)
    if not dir_info["exists"]:
        # Return home directory as fallback
        home = get_home_directory(user)
        # Provide helpful note: directory will be created when project is set up
        fallback_note = f"Directory '{path}' does not exist. It will be created automatically when you create a project here."
        return jsonify(
            {
                "currentPath": path,
                "error": "Directory does not exist",
                "fallback_note": fallback_note,
                "fallback": {
                    "currentPath": home,
                    "parentPath": str(Path(home).parent),
                    "directories": list_subdirectories(home, system_account),
                    "homePath": home,
                    "canCreate": get_directory_info(home, system_account).get("is_writable", False),
                },
            }
        )

    if not dir_info["is_dir"]:
        return jsonify({"error": "Path is not a directory"}), 400

    if not dir_info["is_readable"]:
        return jsonify({"error": "Permission denied"}), 403

    # List subdirectories
    directories = list_subdirectories(path, system_account)

    # Get parent directory
    parent: str | None = str(Path(path).parent)
    if parent == path:  # Root directory
        parent = None

    return jsonify(
        {
            "currentPath": path,
            "parentPath": parent,
            "directories": directories,
            "homePath": get_home_directory(user),
            "canCreate": dir_info["is_writable"],
        }
    )


def list_subdirectories(path: str, system_account: str | None = None) -> list:
    """List subdirectories in a path, optionally as a specific user."""
    directories: list[dict[str, Any]] = []

    # Check if current process user is already the target user
    # This avoids sudo failures when NoNewPrivileges=true is set
    effective_system_account = get_effective_system_account(system_account)

    try:
        if effective_system_account:
            # Use sudo to list directory as the specified user
            result = run_as_user(effective_system_account, ["ls", "-1", path])
            if result.returncode != 0:
                logger.warning(f"Permission denied accessing {path} as {system_account}")
                return directories

            entries = result.stdout.strip().split("\n") if result.stdout.strip() else []

            for entry in entries:
                full_path = os.path.join(path, entry)

                # Skip hidden files/directories (except .qwen for special case)
                if entry.startswith(".") and entry != ".qwen":
                    continue

                # Check if it's a directory
                dir_result = run_as_user(effective_system_account, ["test", "-d", full_path])
                if dir_result.returncode != 0:
                    continue

                # Check permissions
                readable_result = run_as_user(effective_system_account, ["test", "-r", full_path])
                writable_result = run_as_user(effective_system_account, ["test", "-w", full_path])

                directories.append(
                    {
                        "name": entry,
                        "path": full_path,
                        "isReadable": readable_result.returncode == 0,
                        "isWritable": writable_result.returncode == 0,
                    }
                )
        else:
            # Fallback to process user's permissions
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)

                # Skip hidden files/directories (except .qwen for special case)
                if entry.startswith(".") and entry != ".qwen":
                    continue

                # Only include directories
                if os.path.isdir(full_path):
                    try:
                        is_readable = os.access(full_path, os.R_OK)
                        is_writable = os.access(full_path, os.W_OK)

                        directories.append(
                            {
                                "name": entry,
                                "path": full_path,
                                "isReadable": is_readable,
                                "isWritable": is_writable,
                            }
                        )
                    except Exception:
                        # Skip directories we can't access
                        continue

    except PermissionError:
        logger.warning(f"Permission denied accessing {path}")
    except Exception as e:
        logger.error(f"Error listing directory {path}: {e}")

    # Sort directories alphabetically
    directories.sort(key=lambda d: d["name"].lower())

    return directories


@fs_bp.route("/fs/check-path", methods=["POST"])
def api_check_path():
    """Check if a path is valid and can be used for a project."""
    user = g.user

    data = request.get_json() or {}
    path = data.get("path")

    if not path:
        return jsonify({"error": "Path is required"}), 400

    # Validate path format — restrict to workspace base dirs
    base_dirs = get_workspace_base_dirs()
    if not is_valid_path(path, allowed_prefixes=base_dirs):
        allowed_paths = ", ".join(base_dirs)
        return (
            jsonify(
                {
                    "valid": False,
                    "error": f"Path must be under one of: {allowed_paths}. Provided path: {path}",
                }
            ),
            400,
        )

    path = os.path.realpath(path)

    # Get system account to check permissions as the correct user
    system_account = user.get("system_account") if user else None

    # Check path status
    dir_info = get_directory_info(path, system_account)

    if dir_info["exists"]:
        if not dir_info["is_dir"]:
            return jsonify(
                {
                    "valid": False,
                    "exists": True,
                    "error": "Path exists but is not a directory",
                }
            )
        return jsonify(
            {
                "valid": True,
                "exists": True,
                "canWrite": dir_info["is_writable"],
            }
        )
    else:
        # Check if parent directory is writable
        parent = str(Path(path).parent)
        parent_info = get_directory_info(parent, system_account)

        if not parent_info["exists"]:
            return jsonify(
                {
                    "valid": False,
                    "exists": False,
                    "error": "Parent directory does not exist",
                }
            )

        if not parent_info["is_writable"]:
            return jsonify(
                {
                    "valid": False,
                    "exists": False,
                    "error": "Cannot create directory (parent not writable)",
                }
            )

        return jsonify(
            {
                "valid": True,
                "exists": False,
                "canCreate": True,
            }
        )


@fs_bp.route("/fs/home", methods=["GET"])
def api_get_home():
    """Get user's home directory."""
    user = g.user

    system_account = user.get("system_account") if user else None
    home = get_home_directory(user)
    dir_info = get_directory_info(home, system_account)

    return jsonify(
        {
            "homePath": home,
            "canCreate": dir_info["is_writable"],
        }
    )


@fs_bp.route("/fs/create-directory", methods=["POST"])
def api_create_directory():
    """Create a directory on the local file system.

    Used by qwen-code-webui to create project directories when user
    selects "New Folder" in the project selector.

    Expects JSON body with 'path' (full path of the directory to create).
    Optionally 'system_account' to create as a specific user (admin only).
    """
    user = g.user

    data = request.get_json() or {}
    dir_path = data.get("path", "")

    if not dir_path:
        return jsonify({"success": False, "error": "Path is required"}), 400

    if len(dir_path) > 4096:
        return jsonify({"success": False, "error": "Path too long"}), 400

    # Validate path format — restrict to workspace base dir(s)
    base_dirs = get_workspace_base_dirs()
    if not is_valid_path(dir_path, allowed_prefixes=base_dirs):
        # Provide specific error message with allowed paths
        allowed_paths = ", ".join(base_dirs)
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Path must be under one of: {allowed_paths}. Provided path: {dir_path}",
                }
            ),
            400,
        )

    dir_path = os.path.realpath(dir_path)

    # Get system_account for sudo operations
    system_account = user.get("system_account") if user else None

    # Check if path already exists
    dir_info = get_directory_info(dir_path, system_account)
    if dir_info["exists"]:
        if dir_info["is_dir"]:
            return jsonify(
                {
                    "success": True,
                    "path": dir_path,
                    "message": "Directory already exists",
                }
            )
        else:
            return jsonify({"success": False, "error": "Path exists but is not a directory"}), 400

    # Check if parent directory is writable
    parent = str(Path(dir_path).parent)
    parent_info = get_directory_info(parent, system_account)

    if not parent_info["exists"]:
        return jsonify({"success": False, "error": "Parent directory does not exist"}), 400

    if not parent_info["is_writable"]:
        return jsonify({"success": False, "error": "Parent directory is not writable"}), 403

    # Create the directory
    try:
        effective_system_account = get_effective_system_account(system_account)
        if effective_system_account:
            # Use sudo to create directory as the specified user
            result = run_as_user(effective_system_account, ["mkdir", "-p", dir_path])
            if result.returncode != 0:
                logger.error(f"Failed to create directory as {system_account}: {result.stderr}")
                return (
                    jsonify(
                        {"success": False, "error": f"Failed to create directory: {result.stderr}"}
                    ),
                    403,
                )
            logger.info(f"Created directory as {system_account}: {dir_path}")
        else:
            # Already running as target user or no system_account, use direct permissions
            os.makedirs(dir_path, exist_ok=True)
            logger.info(f"Created directory: {dir_path}")

        return jsonify(
            {
                "success": True,
                "path": dir_path,
                "message": "Directory created successfully",
            }
        )
    except PermissionError:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Timeout creating directory"}), 500
    except Exception as e:
        logger.error(f"Error creating directory: {e}")
        return jsonify({"success": False, "error": f"Failed to create directory: {e}"}), 500
