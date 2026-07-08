"""
Open ACE - Path Validator

Path validation utilities for file operations.
Provides filename sanitization and path validation.
"""

import os
import platform
import re
from typing import Optional

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


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize filename by removing dangerous characters.

    Removes path separators, special characters, and prevents
    hidden files. This is a security measure against path traversal
    and other filename-based attacks.

    Args:
        filename: Original filename.
        max_length: Maximum allowed filename length.

    Returns:
        str: Sanitized filename safe for file system use.
    """
    if not filename:
        return "unnamed"

    # Remove path separators and special characters
    # Keep only alphanumeric, underscore, hyphen, and dot
    sanitized = re.sub(r'[^\w\-_.]', '_', filename)

    # Prevent hidden files (starting with dot)
    if sanitized.startswith('.'):
        sanitized = '_' + sanitized[1:]

    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)

    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')

    # Ensure we have something left
    if not sanitized:
        return "unnamed"

    # Limit length
    if len(sanitized) > max_length:
        # Keep extension if present
        if '.' in sanitized:
            ext = sanitized.rsplit('.', 1)[-1]
            base = sanitized[:max_length - len(ext) - 1]
            sanitized = f"{base}.{ext}"
        else:
            sanitized = sanitized[:max_length]

    return sanitized


def is_valid_path(path: str, allowed_prefixes: Optional[list[str]] = None) -> bool:
    """
    Check if path is valid for file operations.

    Optionally restricts the resolved path to a list of allowed prefix
    directories. If allowed_prefixes is None, no prefix restriction is
    applied (backward compatible).

    Also checks against system-sensitive directory blacklist to prevent
    users from writing to /etc, /bin, /root, etc.

    Args:
        path: Path to validate.
        allowed_prefixes: Optional list of allowed directory prefixes.

    Returns:
        bool: True if path is valid and safe.
    """
    if not path:
        return False

    # Check for path traversal in the original input
    if ".." in path:
        return False

    # Resolve to absolute path, following symlinks to detect traversal
    try:
        abs_path = os.path.realpath(path)
    except Exception:
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

        # Blacklist check for Linux/Mac - protect system directories
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


def is_path_blacklisted(path: str) -> bool:
    """
    Check if path is in the system blacklist.

    Args:
        path: Path to check.

    Returns:
        bool: True if path is blacklisted.
    """
    if not path:
        return False

    try:
        abs_path = os.path.realpath(path)
    except Exception:
        return False

    for blocked in _BLACKLISTED_RESOLVED:
        if abs_path == blocked or abs_path.startswith(blocked + os.sep):
            return True

    return False


def ensure_directory_exists(path: str) -> bool:
    """
    Ensure directory exists, creating if necessary.

    Args:
        path: Directory path.

    Returns:
        bool: True if directory exists or was created.
    """
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def get_safe_upload_path(base_path: str, user_id: int, session_id: Optional[str] = None) -> str:
    """
    Generate safe upload path for user files.

    Creates a user-isolated directory under the base path.
    Optionally includes session_id for session-specific isolation.

    Args:
        base_path: Base upload directory.
        user_id: User ID for isolation.
        session_id: Optional session ID for further isolation.

    Returns:
        str: Safe upload path.
    """
    # Ensure base_path is not blacklisted
    if is_path_blacklisted(base_path):
        raise ValueError(f"Base path '{base_path}' is in system blacklist")

    # Build user-isolated path
    if session_id:
        upload_path = os.path.join(base_path, str(user_id), sanitize_filename(session_id))
    else:
        upload_path = os.path.join(base_path, str(user_id))

    # Validate the path
    if not is_valid_path(upload_path, allowed_prefixes=[os.path.realpath(base_path)]):
        raise ValueError(f"Generated path '{upload_path}' is invalid")

    return upload_path


def expand_storage_path(path: str) -> str:
    """
    Expand storage path, handling ~ for home directory.

    Args:
        path: Storage path (may contain ~).

    Returns:
        str: Expanded absolute path.
    """
    if path.startswith("~"):
        path = os.path.expanduser(path)
    return os.path.abspath(path)