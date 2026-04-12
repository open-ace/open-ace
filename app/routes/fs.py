#!/usr/bin/env python3
"""
Open ACE - File System Routes

API routes for file system browsing operations.
Used by project selector UI to browse directories.
"""

import logging
import os
import platform
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request

from app.services.auth_service import AuthService
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

fs_bp = Blueprint("fs", __name__)
auth_service = AuthService()
user_repo = UserRepository()


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

    valid, session_or_error = auth_service.validate_session(token)
    if not valid:
        return None, session_or_error, 401

    user_id = session_or_error.get("user_id")
    user = user_repo.get_user_by_id(user_id)
    return user, None, 200


def get_webui_user():
    """Get user from webui token (for iframe integration)."""
    from app.services.webui_manager import get_webui_manager

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.args.get("token")

    if not token:
        return None, {"error": "Unauthorized"}, 401

    manager = get_webui_manager()
    if not manager:
        return None, {"error": "WebUI manager not available"}, 500

    valid, user_id, error = manager.validate_token(token)
    if not valid:
        return None, {"error": error}, 401

    user = user_repo.get_user_by_id(user_id)
    return user, None, 200


def get_workspace_base_dir() -> str:
    """Get the workspace base directory. Configurable via WORKSPACE_BASE_DIR env var."""
    return os.environ.get("WORKSPACE_BASE_DIR", "/home")


def get_home_directory(user=None):
    """Get user's home directory based on system_account."""
    base_dir = get_workspace_base_dir()
    if user:
        system_account = user.get("system_account") or user.get("username")
        if system_account:
            # Return the system account's workspace directory
            user_home = f"{base_dir}/{system_account}"
            # Use sudo to check if directory exists
            result = run_as_user(system_account, ["test", "-e", user_home])
            if result.returncode == 0:
                return user_home
    # Fallback to process user's home
    return str(Path.home())


def is_valid_path(path: str) -> bool:
    """Check if path is valid for browsing."""
    if not path:
        return False

    # Resolve to absolute path
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False

    # Check for path traversal
    if ".." in path:
        return False

    # Platform-specific validation
    system = platform.system()
    if system == "Windows":
        # Windows: must be a valid drive path
        if not (len(abs_path) >= 2 and abs_path[1] == ":"):
            return False
    else:
        # Mac/Linux: must start with /
        if not abs_path.startswith("/"):
            return False

    return True


def get_directory_info(path: str, system_account: str = None):
    """Get information about a directory, optionally as a specific user."""
    try:
        if system_account:
            # Use sudo to check permissions as the specified user
            # Check if directory exists
            result = run_as_user(system_account, ["test", "-e", path])
            if result.returncode != 0:
                return {
                    "exists": False,
                    "is_dir": False,
                    "is_readable": False,
                    "is_writable": False,
                }

            # Check if it's a directory
            result = run_as_user(system_account, ["test", "-d", path])
            is_dir = result.returncode == 0

            if not is_dir:
                return {
                    "exists": True,
                    "is_dir": False,
                    "is_readable": False,
                    "is_writable": False,
                }

            # Check if readable
            result = run_as_user(system_account, ["test", "-r", path])
            is_readable = result.returncode == 0

            # Check if writable
            result = run_as_user(system_account, ["test", "-w", path])
            is_writable = result.returncode == 0

            return {
                "exists": True,
                "is_dir": is_dir,
                "is_readable": is_readable,
                "is_writable": is_writable,
                "size": 0,
            }
        else:
            # Fallback to process user's permissions
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
            "error": str(e),
        }


@fs_bp.route("/fs/browse", methods=["GET"])
def api_browse_directory():
    """Browse a directory and list subdirectories."""
    # Try webui token first (for iframe integration)
    user, error, code = get_webui_user()
    if not user:
        # Try regular session
        user, error, code = get_current_user()
        if not user:
            return jsonify(error), code

    # Get system_account for sudo operations
    system_account = user.get("system_account") if user else None

    # Get path parameter
    path = request.args.get("path", "")

    # Handle special path values
    if not path or path.lower() == "home":
        path = get_home_directory(user)
    else:
        # Validate and resolve path
        if not is_valid_path(path):
            return jsonify({"error": "Invalid path"}), 400

        path = os.path.abspath(path)

    # Check if path exists and is readable
    dir_info = get_directory_info(path, system_account)
    if not dir_info["exists"]:
        # Return home directory as fallback
        home = get_home_directory(user)
        return jsonify({
            "currentPath": path,
            "error": "Directory does not exist",
            "fallback": {
                "currentPath": home,
                "parentPath": str(Path(home).parent),
                "directories": list_subdirectories(home, system_account),
                "homePath": home,
                "canCreate": get_directory_info(home, system_account).get("is_writable", False),
            }
        })

    if not dir_info["is_dir"]:
        return jsonify({"error": "Path is not a directory"}), 400

    if not dir_info["is_readable"]:
        return jsonify({"error": "Permission denied"}), 403

    # List subdirectories
    directories = list_subdirectories(path, system_account)

    # Get parent directory
    parent = str(Path(path).parent)
    if parent == path:  # Root directory
        parent = None

    return jsonify({
        "currentPath": path,
        "parentPath": parent,
        "directories": directories,
        "homePath": get_home_directory(user),
        "canCreate": dir_info["is_writable"],
    })


def list_subdirectories(path: str, system_account: str = None) -> list:
    """List subdirectories in a path, optionally as a specific user."""
    directories = []

    try:
        if system_account:
            # Use sudo to list directory as the specified user
            result = run_as_user(system_account, ["ls", "-1", path])
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
                dir_result = run_as_user(system_account, ["test", "-d", full_path])
                if dir_result.returncode != 0:
                    continue

                # Check permissions
                readable_result = run_as_user(system_account, ["test", "-r", full_path])
                writable_result = run_as_user(system_account, ["test", "-w", full_path])

                directories.append({
                    "name": entry,
                    "path": full_path,
                    "isReadable": readable_result.returncode == 0,
                    "isWritable": writable_result.returncode == 0,
                })
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

                        directories.append({
                            "name": entry,
                            "path": full_path,
                            "isReadable": is_readable,
                            "isWritable": is_writable,
                        })
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
    user, error, code = get_webui_user()
    if not user:
        user, error, code = get_current_user()
        if not user:
            return jsonify(error), code

    data = request.get_json() or {}
    path = data.get("path")

    if not path:
        return jsonify({"error": "Path is required"}), 400

    # Validate path format
    if not is_valid_path(path):
        return jsonify({
            "valid": False,
            "error": "Invalid path format",
        }), 400

    path = os.path.abspath(path)

    # Check path status
    dir_info = get_directory_info(path)

    if dir_info["exists"]:
        if not dir_info["is_dir"]:
            return jsonify({
                "valid": False,
                "exists": True,
                "error": "Path exists but is not a directory",
            })
        return jsonify({
            "valid": True,
            "exists": True,
            "canWrite": dir_info["is_writable"],
        })
    else:
        # Check if parent directory is writable
        parent = str(Path(path).parent)
        parent_info = get_directory_info(parent)

        if not parent_info["exists"]:
            return jsonify({
                "valid": False,
                "exists": False,
                "error": "Parent directory does not exist",
            })

        if not parent_info["is_writable"]:
            return jsonify({
                "valid": False,
                "exists": False,
                "error": "Cannot create directory (parent not writable)",
            })

        return jsonify({
            "valid": True,
            "exists": False,
            "canCreate": True,
        })


@fs_bp.route("/fs/home", methods=["GET"])
def api_get_home():
    """Get user's home directory."""
    user, error, code = get_webui_user()
    if not user:
        user, error, code = get_current_user()
        if not user:
            return jsonify(error), code

    system_account = user.get("system_account") if user else None
    home = get_home_directory(user)
    dir_info = get_directory_info(home, system_account)

    return jsonify({
        "homePath": home,
        "canCreate": dir_info["is_writable"],
    })