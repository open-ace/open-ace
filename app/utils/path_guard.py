"""Shared path validation for user-supplied filesystem paths.

Moved from app/routes/fs.py (Issue #3376) so the remote terminal/vscode
start endpoints can reuse the same traversal/blacklist semantics. Behavior
is identical to the previous fs.py definitions.
"""

from __future__ import annotations

import os
import platform

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
    "/lib64",  # 64-bit shared libraries
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
