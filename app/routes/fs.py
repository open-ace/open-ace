from __future__ import annotations

"""
Open ACE - File System Routes

API routes for file system browsing operations.
Used by project selector UI to browse directories.
"""

import logging
import mimetypes
import os
import platform
import pwd
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.repositories.user_repo import UserRepository
from app.utils.workspace import (OPENACE_CHOWN_WRAPPER, _is_wrapper_available,
                                 get_workspace_base_dir,
                                 get_workspace_base_dirs,
                                 run_as_root_if_needed)

logger = logging.getLogger(__name__)

fs_bp = Blueprint("fs", __name__)
user_repo = UserRepository()

# --- File upload/download config ---
# Per-endpoint size cap (MB). Do NOT use the global app MAX_CONTENT_LENGTH — it
# would 413 other authenticated upload endpoints (avatars, /api/upload/*). See
# app/__init__.py:199-205. The frontend mirror is MAX_UPLOAD_SIZE_MB in
# frontend/src/api/fs.ts; keep them in sync.
MAX_UPLOAD_SIZE_MB = int(os.environ.get("OPENACE_MAX_UPLOAD_SIZE_MB", "100"))

# Filename sanitization: strip control chars and path separators. We use
# basename() upstream too, but this defends in depth against embedded
# separators / NULs that could slip past naive handling.
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\\/:]")

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
    from app.auth.decorators import (_extract_token, _load_user_from_token,
                                     enforce_password_change_requirement,
                                     normalize_webui_token)

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
        # Handle double-encoded tokens from some clients
        url_token = normalize_webui_token(url_token)
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
    from app.auth.decorators import normalize_webui_token
    from app.services.webui_manager import get_webui_manager

    token: str | None = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not token:
        token = request.args.get("token") or ""
        # Handle double-encoded tokens from some clients
        token = normalize_webui_token(token)

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


def _sanitize_filename(name: str) -> str | None:
    """Return a safe basename, or None if the name is unusable.

    - Takes basename() to drop any directory component the client may send.
    - Replaces control characters and path separators with '_'.
    - Rejects empty, '.', and '..'.
    """
    if not name:
        return None
    base = os.path.basename(name)
    base = _UNSAFE_FILENAME_CHARS.sub("_", base).strip().rstrip(".").strip()
    if not base or base in (".", ".."):
        return None
    return base


def _resolve_user_owned_path(target_dir: str, user) -> tuple[str, str | None]:
    """Validate and resolve a directory the user wants to operate on.

    Combines two guards:
    1. is_valid_path — base_dirs prefix + system blacklist (existing reuse).
    2. Home subtree lock (Issue #1813) — target must be inside the current
       user's home directory, so users cannot touch each other's files in
       multi-user deployments.

    Returns (resolved_abs_dir, system_account). system_account is None when
    the process already runs as the target user (no chown needed) — see
    get_effective_system_account.
    """
    if not target_dir:
        raise ValueError("Path is required")

    base_dirs = get_workspace_base_dirs()
    if not is_valid_path(target_dir, allowed_prefixes=base_dirs):
        allowed = ", ".join(base_dirs)
        raise ValueError(f"Path must be under one of: {allowed}")

    resolved = os.path.realpath(target_dir)

    # Home subtree lock: must equal home or live directly beneath it.
    home = get_home_directory(user)
    if resolved != home and not resolved.startswith(home + os.sep):
        raise ValueError("Path must be inside your home directory")

    system_account = (user.get("system_account") if user else None) or None
    return resolved, system_account


def _chown_to_user(path: str, system_account: str | None) -> bool:
    """Change ownership of *path* to *system_account*.

    Used after a root process writes into a per-user workspace so the file
    is owned by the target user (Docker multi-user mode).

    - Root process: os.chown directly (see app/services/webui_manager.py:807-810).
    - Non-root: prefer the openace-chown wrapper (audited, path-checked), fall
      back to sudo chown via run_as_root_if_needed.
    - system_account None: no-op (single-user case), returns True.

    Returns True on success (or no-op). Returns False when uid/gid lookup or
    the chown itself failed — callers MUST treat False as a hard failure so
    the file does not end up owned by the web process user (which would leave
    the target user unable to read/delete their own upload).
    """
    if not system_account:
        return True
    try:
        uid_out = subprocess.run(
            ["id", "-u", system_account], capture_output=True, text=True, timeout=5
        )
        gid_out = subprocess.run(
            ["id", "-g", system_account], capture_output=True, text=True, timeout=5
        )
        if uid_out.returncode != 0 or gid_out.returncode != 0:
            logger.warning(f"Cannot resolve uid/gid for {system_account}")
            return False
        uid = int(uid_out.stdout.strip())
        gid = int(gid_out.stdout.strip())
    except Exception as e:
        logger.warning(f"Cannot resolve uid/gid for {system_account}: {e}")
        return False

    try:
        if os.geteuid() == 0:
            os.chown(path, uid, gid)
            return True
        if _is_wrapper_available(OPENACE_CHOWN_WRAPPER):
            r = subprocess.run(
                [OPENACE_CHOWN_WRAPPER, f"{uid}:{gid}", path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode != 0:
                logger.warning(
                    f"openace-chown failed for {path} -> {system_account}: {r.stderr}"
                )
                return False
            return True
        r = run_as_root_if_needed(["chown", f"{uid}:{gid}", path])
        if getattr(r, "returncode", 0) != 0:
            logger.warning(
                f"chown failed for {path} -> {system_account}: {getattr(r, 'stderr', '')}"
            )
            return False
        return True
    except Exception as e:
        logger.warning(f"chown {path} -> {system_account} failed: {e}")
        return False


def _resolve_file_in_home(
    raw_path: str, user
) -> tuple[str, str | None] | tuple[None, None]:
    """Validate that *raw_path* resolves to a file inside the user's home subtree.

    Single source of truth for download/delete file-path validation. Combines:
      1. is_valid_path (base_dirs prefix + system blacklist, reusing existing logic)
      2. home subtree lock (Issue #1813) via home + os.sep boundary check

    realpath() collapses ``..`` and follows symlinks, so a single check on the
    resolved target is sufficient — no need to validate dirname separately.

    Returns (resolved_abs_path, system_account), or (None, None) if rejected
    (caller should return 400 with the reason in the optional second element —
    kept simple here to match the common case).
    """
    if not raw_path:
        return None, None
    base_dirs = get_workspace_base_dirs()
    if not is_valid_path(raw_path, allowed_prefixes=base_dirs):
        return None, None
    target = os.path.realpath(raw_path)
    home = get_home_directory(user)
    if target != home and not target.startswith(home + os.sep):
        return None, None
    system_account = (user.get("system_account") if user else None) or None
    return target, system_account


def _content_disposition_filename(filename: str) -> str:
    """Build a Content-Disposition value with an RFC 5987 ``filename*``.

    A plain ``filename="...`` breaks if the name contains ``"`` (header parse
    error) or non-ASCII bytes (mojibake on many clients). We emit BOTH a
    sanitized ASCII fallback (quotes/controls stripped) and a percent-encoded
    UTF-8 ``filename*`` per RFC 5987; modern clients prefer the latter.
    """
    from urllib.parse import quote

    # ASCII fallback: strip quotes and control chars so the quoted token stays valid.
    ascii_fallback = (
        "".join(c for c in filename if c.isprintable() and c not in '"\\') or "download"
    )
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


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
    """Browse a directory and list subdirectories (and optionally files)."""
    user = g.user

    # Get system_account for sudo operations
    system_account = user.get("system_account") if user else None

    # include_files is opt-in via ?include_files=1 so existing callers
    # (directory selector, remote workspace fallback) are unaffected.
    include_files = request.args.get("include_files", "").lower() in (
        "1",
        "true",
        "yes",
    )

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
            return (
                jsonify({"error": f"Path must be under one of: {allowed_paths}"}),
                400,
            )

        path = os.path.realpath(path)

    # Check if path exists and is readable
    dir_info = get_directory_info(path, system_account)
    if not dir_info["exists"]:
        # Return home directory as fallback
        home = get_home_directory(user)
        # Provide helpful note: directory will be created when project is set up
        fallback_note = f"Directory '{path}' does not exist. It will be created automatically when you create a project here."
        listing = list_subdirectories(home, system_account, include_files=include_files)
        return jsonify(
            {
                "currentPath": path,
                "error": "Directory does not exist",
                "fallback_note": fallback_note,
                "fallback": {
                    "currentPath": home,
                    "parentPath": str(Path(home).parent),
                    "directories": listing["directories"],
                    "files": listing["files"],
                    "homePath": home,
                    "canCreate": get_directory_info(home, system_account).get(
                        "is_writable", False
                    ),
                },
            }
        )

    if not dir_info["is_dir"]:
        return jsonify({"error": "Path is not a directory"}), 400

    if not dir_info["is_readable"]:
        return jsonify({"error": "Permission denied"}), 403

    # List entries (files only when include_files=True)
    listing = list_subdirectories(path, system_account, include_files=include_files)

    # Get parent directory
    parent: str | None = str(Path(path).parent)
    if parent == path:  # Root directory
        parent = None

    return jsonify(
        {
            "currentPath": path,
            "parentPath": parent,
            "directories": listing["directories"],
            "files": listing["files"],
            "homePath": get_home_directory(user),
            "canCreate": dir_info["is_writable"],
        }
    )


def list_subdirectories(
    path: str,
    system_account: str | None = None,
    include_files: bool = False,
) -> dict[str, list]:
    """List entries in a path, optionally as a specific user.

    Returns ``{"directories": [...], "files": [...]}``. When *include_files*
    is False (the default, for backward compatibility) the ``files`` list is
    always empty and the call has the same cost/behavior as before.

    Directory entries: ``{name, path, isReadable, isWritable}``.
    File entries (only when include_files=True): ``{name, path, size, is_readable}``.
    """
    directories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    # Check if current process user is already the target user
    # This avoids sudo failures when NoNewPrivileges=true is set
    effective_system_account = get_effective_system_account(system_account)

    try:
        if effective_system_account:
            # Use sudo to list directory as the specified user.
            # Performance: we batch stat into a SINGLE sudo call instead of
            # forking per-entry (test -d/-r/-w/stat per file used to be ~3N
            # forks; with 200 files that is hundreds of sudo execs and tens
            # of seconds). ``stat *`` is in the sudoers OPENACE_UTILS
            # whitelist.
            #
            # Permission accuracy (review #2): %A reflects the file *owner's*
            # permission bits, which only matches effective-user access when
            # owner == system_account. We therefore gather %U (owner name)
            # and only trust %A for owner==system_account entries; entries
            # whose owner differs fall back to a per-entry test -r/-w (rare:
            # mostly root-owned .qwen configs / migrated files).
            ls_result = run_as_user(effective_system_account, ["ls", "-1", path])
            if ls_result.returncode != 0:
                logger.warning(
                    f"Permission denied accessing {path} as {system_account}"
                )
                return {"directories": directories, "files": files}

            raw_entries = ls_result.stdout.split("\n") if ls_result.stdout else []
            # Build the candidate list (skip hidden, except .qwen), preserving
            # the original entry names so we can rejoin with full_path.
            candidate_paths: list[str] = []
            name_by_path: dict[str, str] = {}
            for entry in raw_entries:
                entry = entry.strip()
                if not entry:
                    continue
                if entry.startswith(".") and entry != ".qwen":
                    continue
                full_path = os.path.join(path, entry)
                candidate_paths.append(full_path)
                name_by_path[full_path] = entry

            if not candidate_paths:
                return {"directories": directories, "files": files}

            # One stat for ALL candidates. %n=name %U=owner %F=type %s=size
            # %A=perm mode. We parse stdout directly (NOT gated on returncode):
            # a single un-stat-able entry only lands in stderr and should not
            # blank out the whole directory.
            stat_fmt = "%n\t%U\t%F\t%s\t%A"
            stat_result = run_as_user(
                effective_system_account, ["stat", "-c", stat_fmt, *candidate_paths]
            )
            stat_by_path: dict[str, dict[str, str]] = {}
            for line in stat_result.stdout.split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 5:
                    # Filename with embedded tab → columns shift. Skip lines
                    # whose first field isn't a known candidate path so a
                    # malformed name can't pollute other entries.
                    continue
                n, owner, ftype, size_s, perm = (
                    parts[0],
                    parts[1],
                    parts[2],
                    parts[3],
                    parts[4],
                )
                if n not in name_by_path:
                    # Path mismatch (embedded newline split the record, or
                    # stat emitted something unexpected) — ignore safely.
                    continue
                stat_by_path[n] = {
                    "type": ftype,
                    "size": size_s,
                    "perm": perm,
                    "owner": owner,
                }

            # Collect entries whose owner != system_account so we can fall
            # back to accurate access checks for just those (keeps the common
            # case — owner==system_account — on the fast batched path).
            needs_access_check: list[str] = []
            for full_path in candidate_paths:
                st = stat_by_path.get(full_path)
                if st and st.get("owner") != effective_system_account:
                    needs_access_check.append(full_path)

            access_cache: dict[str, dict[str, bool]] = {}
            for full_path in needs_access_check:
                rr = run_as_user(effective_system_account, ["test", "-r", full_path])
                ww = run_as_user(effective_system_account, ["test", "-w", full_path])
                access_cache[full_path] = {
                    "readable": rr.returncode == 0,
                    "writable": ww.returncode == 0,
                }

            for full_path in candidate_paths:
                entry = name_by_path[full_path]
                st = stat_by_path.get(full_path)
                if not st:
                    # stat failed for this entry (e.g. broken symlink) — skip.
                    continue
                is_dir = st["type"] == "directory"

                if full_path in access_cache:
                    # owner != system_account: use the accurate access check.
                    is_readable = access_cache[full_path]["readable"]
                    is_writable = access_cache[full_path]["writable"]
                else:
                    # owner == system_account: %A owner bits are authoritative.
                    # perm looks like "drwxr-xr-x" / "-rw-r--r--"; chars [1:4]
                    # are the owner triplet.
                    owner_bits = st["perm"][1:4] if len(st["perm"]) >= 4 else ""
                    is_readable = "r" in owner_bits
                    is_writable = "w" in owner_bits

                if is_dir:
                    directories.append(
                        {
                            "name": entry,
                            "path": full_path,
                            "isReadable": is_readable,
                            "isWritable": is_writable,
                        }
                    )
                elif include_files:
                    try:
                        size = int(st["size"])
                    except (ValueError, TypeError):
                        size = 0
                    files.append(
                        {
                            "name": entry,
                            "path": full_path,
                            "size": size,
                            "is_readable": is_readable,
                        }
                    )
        else:
            # Fallback to process user's permissions
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)

                # Skip hidden files/directories (except .qwen for special case)
                if entry.startswith(".") and entry != ".qwen":
                    continue

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
                elif include_files and os.path.isfile(full_path):
                    try:
                        files.append(
                            {
                                "name": entry,
                                "path": full_path,
                                "size": os.path.getsize(full_path),
                                "is_readable": os.access(full_path, os.R_OK),
                            }
                        )
                    except OSError:
                        continue

    except PermissionError:
        logger.warning(f"Permission denied accessing {path}")
    except Exception as e:
        logger.error(f"Error listing directory {path}: {e}")

    # Sort alphabetically
    directories.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())

    return {"directories": directories, "files": files}


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
            return (
                jsonify(
                    {"success": False, "error": "Path exists but is not a directory"}
                ),
                400,
            )

    # Check if parent directory is writable
    parent = str(Path(dir_path).parent)
    parent_info = get_directory_info(parent, system_account)

    if not parent_info["exists"]:
        return (
            jsonify({"success": False, "error": "Parent directory does not exist"}),
            400,
        )

    if not parent_info["is_writable"]:
        return (
            jsonify({"success": False, "error": "Parent directory is not writable"}),
            403,
        )

    # Create the directory
    try:
        effective_system_account = get_effective_system_account(system_account)
        if effective_system_account:
            # Use sudo to create directory as the specified user
            result = run_as_user(effective_system_account, ["mkdir", "-p", dir_path])
            if result.returncode != 0:
                logger.error(
                    f"Failed to create directory as {system_account}: {result.stderr}"
                )
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Failed to create directory: {result.stderr}",
                        }
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
        return (
            jsonify({"success": False, "error": f"Failed to create directory: {e}"}),
            500,
        )


# ============================================================
# File upload / download / delete (personal files page).
#
# Multi-user sudo strategy (see docs/personal-files-upload.md):
# - Docker multi-user mode: process runs as root. We write to a temp file
#   inside the target dir, chown it to the target system_account, then
#   atomically rename. No sudo is involved — root uses kernel capabilities
#   (os.chown / os.replace / os.remove) directly. This deliberately avoids
#   the sudoers whitelist (which does not include cp/cat/rm/tee).
# - Single-user / non-root package mode: the process already owns its home
#   tree, so we write/read/remove directly.
# - os.chown is root-only; non-root uses the openace-chown wrapper as a
#   fallback (see app/utils/workspace.py).
# ============================================================


@fs_bp.route("/fs/upload", methods=["POST"])
def api_upload_file():
    """Upload a file into the user's home subtree (personal files page).

    Form fields: ``file`` (multipart), ``path`` (target directory).
    The file is written as a sibling temp file then atomically renamed.
    Size is capped by MAX_UPLOAD_SIZE_MB (checked in-view, not globally).
    """
    user = g.user

    # Cheap pre-filter: reject declared-oversized requests before the body is
    # fully buffered to disk. The Content-Length header can be spoofed, so the
    # authoritative check below still uses the real byte count from seek().
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    declared = request.content_length or 0
    if declared > max_bytes:
        return jsonify({"error": f"File too large (max {MAX_UPLOAD_SIZE_MB}MB)"}), 413

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Authoritative size check (in-view, per app/__init__.py:199-205 we must
    # NOT set a global MAX_CONTENT_LENGTH). werkzeug FileStorage supports seek().
    try:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
    except Exception as e:
        return jsonify({"error": f"Cannot read upload: {e}"}), 400
    if size > max_bytes:
        return jsonify({"error": f"File too large (max {MAX_UPLOAD_SIZE_MB}MB)"}), 413

    # Path + home subtree lock
    target_dir = request.form.get("path", "")
    try:
        resolved_dir, system_account = _resolve_user_owned_path(target_dir, user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Filename + final path re-validation (defends against basename tricks)
    safe_name = _sanitize_filename(file.filename)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400
    target_path = os.path.realpath(os.path.join(resolved_dir, safe_name))
    base_dirs = get_workspace_base_dirs()
    if not is_valid_path(target_path, allowed_prefixes=base_dirs):
        return jsonify({"error": "Invalid target path"}), 400

    # Ensure target directory is writable by the process
    dir_info = get_directory_info(resolved_dir, system_account)
    if not dir_info.get("is_writable"):
        return jsonify({"error": "Target directory is not writable"}), 403

    try:
        if os.geteuid() == 0 and system_account:
            # Docker multi-user (root): temp file → chown → atomic rename.
            # Temp file lives in the same dir so os.replace stays on one
            # filesystem (rename across mounts is not atomic).
            fd, tmp_path = tempfile.mkstemp(
                dir=resolved_dir, prefix=".openace-upload-", suffix=".tmp"
            )
            os.close(fd)
            try:
                file.save(tmp_path)
                if not _chown_to_user(tmp_path, system_account):
                    # chown failed: without it the file would be owned by the
                    # web (root) process and the target user could not read
                    # or delete their own upload. Roll back and fail hard.
                    _safe_remove(tmp_path)
                    return (
                        jsonify(
                            {"error": "Failed to set file ownership for target user"}
                        ),
                        500,
                    )
                os.replace(tmp_path, target_path)
            except Exception:
                _safe_remove(tmp_path)
                raise
        else:
            # Single-user / non-root: write directly to the final path.
            file.save(target_path)

        logger.info(f"Uploaded file: {target_path} ({size} bytes)")
        return jsonify({"success": True, "path": target_path, "size": size})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except OSError as e:
        logger.error(f"Upload failed for {target_path}: {e}")
        return jsonify({"error": f"Upload failed: {e}"}), 500
    except Exception as e:
        logger.error(f"Upload error for {target_path}: {e}")
        return jsonify({"error": f"Upload failed: {e}"}), 500


def _is_direct_access(system_account: str | None) -> bool:
    """Whether file ops can use direct os calls (no sudo needed).

    True when the process is root (bypasses DAC) or already runs as the
    target user (single-user mode). False for non-root multi-user, where
    the process is a service account that cannot traverse the target
    user's home directory and must delegate via ``sudo -u``.
    """
    if os.geteuid() == 0:
        return True
    return get_effective_system_account(system_account) is None


def _check_file_as_user(
    target_path: str, system_account: str | None, flag: str
) -> bool:
    """Run ``test <flag> <target_path>`` as the correct user.

    *flag* is one of ``-f`` (is regular file), ``-r`` (readable),
    ``-w`` (writable). Uses direct ``os`` calls for root / single-user
    (see ``_is_direct_access``) and ``sudo -u`` for non-root multi-user.

    ``test`` is in the sudoers ``OPENACE_UTILS`` whitelist, so this works
    in both Docker and package-method multi-user deployments.
    """
    if _is_direct_access(system_account):
        if flag == "-f":
            return os.path.isfile(target_path)
        if flag == "-r":
            return os.access(target_path, os.R_OK)
        if flag == "-w":
            return os.access(target_path, os.W_OK)
        return False

    effective = get_effective_system_account(system_account)
    assert effective is not None  # _is_direct_access 为 False 时 effective 必定非 None
    result = run_as_user(effective, ["test", flag, target_path])
    return result.returncode == 0


def _filesize_as_user(target_path: str, system_account: str | None) -> int | None:
    """Get file size in bytes as the correct user, or None on failure."""
    if _is_direct_access(system_account):
        try:
            return os.path.getsize(target_path)
        except OSError:
            return None

    effective = get_effective_system_account(system_account)
    assert effective is not None  # _is_direct_access 为 False 时 effective 必定非 None
    result = run_as_user(effective, ["stat", "-c", "%s", target_path])
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except (ValueError, TypeError):
        return None


def _stream_file_as_user(target_path: str, system_account: str | None):
    """Yield 64KB chunks of *target_path* read as the correct user.

    Root / single-user: direct ``open()``. Non-root multi-user: stream
    ``sudo -u <user> cat <path>`` stdout via Popen (``cat`` is in the
    sudoers ``OPENACE_UTILS`` whitelist in package-method; Docker
    multi-user runs as root so never hits this branch).
    """
    if _is_direct_access(system_account):
        with open(target_path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        return

    effective = get_effective_system_account(system_account)
    assert effective is not None  # _is_direct_access 为 False 时 effective 必定非 None
    # Don't use run_as_user() here — it uses subprocess.run with a 10s
    # timeout and capture_output=True, which would buffer the whole file
    # in memory and time out on large downloads. Popen lets us stream.
    proc = subprocess.Popen(
        ["sudo", "-u", effective, "cat", target_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
        if proc.returncode != 0:
            logger.warning(
                f"cat stream for {target_path} as {effective} exited "
                f"with {proc.returncode}"
            )


@fs_bp.route("/fs/download", methods=["GET"])
def api_download_file():
    """Stream a file from the user's home subtree as an attachment.

    Uses a 64KB-chunk generator so large files do not load fully into memory.
    Root reads any path (bypassing DAC); single-user reads its own home;
    non-root multi-user delegates to ``sudo -u <system_account>`` so the
    file check and read execute as the file owner (Issue #1902).
    """
    user = g.user

    raw_path = request.args.get("path", "")
    target_path, system_account = _resolve_file_in_home(raw_path, user)
    if target_path is None:
        return jsonify({"error": "Invalid path (must be a file in your home)"}), 400

    # Permission checks must run as the file owner: os.path.isfile() /
    # os.access() use the process user's credentials and silently return
    # False when it cannot traverse a 0700 home dir, producing a misleading
    # "Not a file" error (Issue #1902).
    if not _check_file_as_user(target_path, system_account, "-f"):
        return jsonify({"error": "Not a file"}), 400
    if not _check_file_as_user(target_path, system_account, "-r"):
        return jsonify({"error": "Permission denied"}), 403

    filename = os.path.basename(target_path)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    size = _filesize_as_user(target_path, system_account)
    if size is None:
        return jsonify({"error": "Cannot stat file"}), 500

    return Response(
        stream_with_context(_stream_file_as_user(target_path, system_account)),
        mimetype=mime,
        headers={
            "Content-Disposition": _content_disposition_filename(filename),
            "Content-Length": str(size),
            "Cache-Control": "no-store",
        },
    )


@fs_bp.route("/fs/delete-file", methods=["POST"])
def api_delete_file():
    """Delete a file from the user's home subtree.

    JSON body: ``{"path": "/abs/path"}``. Only files (not directories) are
    accepted — directory removal is intentionally out of scope.

    The ``os.path.isfile()`` pre-check and the actual ``os.remove()`` must
    both execute as the file owner in multi-user mode (Issue #1902).
    """
    user = g.user

    data = request.get_json(silent=True) or {}
    raw_path = data.get("path", "")
    target_path, system_account = _resolve_file_in_home(raw_path, user)
    if target_path is None:
        return jsonify({"error": "Invalid path (must be a file in your home)"}), 400

    if not _check_file_as_user(target_path, system_account, "-f"):
        return jsonify({"error": "Not a file"}), 400

    try:
        if _is_direct_access(system_account):
            os.remove(target_path)
        else:
            effective = get_effective_system_account(system_account)
            assert (
                effective is not None
            )  # _is_direct_access 为 False 时 effective 必定非 None
            result = run_as_user(effective, ["rm", "--", target_path])
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(f"rm as {effective} failed for {target_path}: {stderr}")
                # sudoers policy denial vs. filesystem permission error
                if "not allowed" in stderr.lower() or "sudo" in stderr.lower():
                    return (
                        jsonify(
                            {
                                "error": "Cannot delete file as owner "
                                "(sudoers policy missing 'rm' for this deployment)"
                            }
                        ),
                        500,
                    )
                return jsonify({"error": "Permission denied"}), 403
        logger.info(f"Deleted file: {target_path}")
        return jsonify({"success": True, "path": target_path})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except OSError as e:
        logger.error(f"Delete failed for {target_path}: {e}")
        return jsonify({"error": f"Delete failed: {e}"}), 500


def _safe_remove(path: str) -> None:
    """Best-effort remove; ignore errors (used for temp file cleanup)."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
