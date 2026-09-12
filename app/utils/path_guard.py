"""Shared path validation for user-supplied filesystem paths.

Moved from app/routes/fs.py (Issue #3376) so the remote terminal/vscode
start endpoints can reuse the same traversal/blacklist semantics. Behavior
is identical to the previous fs.py definitions.
"""

from __future__ import annotations

import os
import platform
import re

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


# Windows drive-absolute path, e.g. C:\workspace or C:/workspace.
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_valid_remote_path(value) -> bool:
    r"""Structural validation for a path that lives on a REMOTE machine.

    Review round 1 (#3376): the backend cannot resolve remote paths, so
    ``is_valid_path``'s realpath / backend-platform / backend-blacklist
    semantics are wrong here (they rejected the frontend's own defaults
    ``C:\\workspace``, ``~/workspace``, ``/root/workspace`` because the
    backend's blacklist contains /root). This validator only enforces
    shape, delegating location policy to the remote agent:

    - a non-empty string without NUL bytes;
    - no ``..`` path segment (either separator flavor);
    - POSIX-absolute (leading ``/``), home-relative (``~/`` or ``~\\``),
      or Windows drive-absolute (``C:\\`` / ``C:/``).
    """
    if not isinstance(value, str):
        return False
    if not value:
        return False
    if "\x00" in value:
        return False
    if any(segment == ".." for segment in re.split(r"[\\/]", value)):
        return False
    return bool(
        value.startswith("/")
        or value.startswith("~/")
        or value.startswith("~\\")
        or _WINDOWS_DRIVE_RE.match(value)
    )


# Directory name of the first-class shared namespace under each workspace
# base dir (review round 3, #3376): ``<base>/shared/<name>`` is the only
# place where a NEW shared project can be registered without an existing
# shared root to anchor on (fresh-deployment bootstrap), because it lies
# outside every user's home subtree and therefore survives the read-side
# home lock in fs.py.
SHARED_NAMESPACE_DIRNAME = "shared"


def shared_namespace_roots(base_dirs: list[str] | None) -> list[str]:
    """Realpath'd shared-namespace roots, one per workspace base dir.

    Review round 3 (#3376, PR #3380): creation-side callers add these to
    the creator's admissible roots (``creator_roots``) so a shared project
    can be registered as ``<base>/shared/<name>`` on a fresh deployment —
    round 2 required an already-open shared root to anchor on, which no
    new deployment could ever produce. The namespace deliberately lives
    OUTSIDE every user home, so the read-side filter (which drops any
    shared row inside a user home subtree) never drops it.
    """
    return [
        os.path.realpath(f"{base.rstrip('/')}/{SHARED_NAMESPACE_DIRNAME}")
        for base in (base_dirs or [])
        if base
    ]


def shared_project_path_error(
    path: str,
    base_dirs: list[str],
    home_dirs: list[str],
    creator_roots: list[str] | None = None,
) -> str | None:
    """Validate a shared-project path against the workspace topology.

    Review round 1 (#3376): a shared project path extends every tenant
    member's browse roots (``fs._allowed_roots_for_user``), so creating one
    must not widen those roots past what the creator already owns. A
    tenant member registering e.g. the workspace base dir itself — or
    another user's home — as a "shared project" would make every other
    home browsable by the whole tenant. Rejected shapes:

    - anything ``is_valid_path`` rejects with *base_dirs* prefixes (``..``,
      system-blacklisted, outside all workspace base dirs);
    - a workspace base dir itself (equal, not just beneath);
    - an ancestor of (or equal to) any user home directory, including the
      creator's own.

    Review round 2 (#3376, 3994613216): round 1 only rejected paths that
    WERE a home or an ancestor of one — a DESCendant of another user's
    home (e.g. ``<base>/alice/.ssh``) passed and became a tenant-wide
    browse root. Two additional rules, applied per side:

    - *home subtree (any depth)*: a path inside a user home subtree is
      rejected, EXCEPT when that home is itself one of *creator_roots*
      (creation side: the creator may share subpaths of their own home,
      nobody else's). With *creator_roots* omitted (read-side filter) no
      home subtree is admissible at all — defense in depth that does not
      depend on the projects row carrying a trustworthy creator.
    - *ownership*: when *creator_roots* is given (creation side), the path
      must fall inside one of them — the creator's own per-base home roots
      plus shared roots already open to them. Registering arbitrary
      workspace paths (e.g. a first-level ``<base>/team-proj``) is no
      longer admissible; an empty *creator_roots* rejects everything
      (fail closed).

    Review round 3 (#3376, PR #3380): round 2 made the two sides accept
    DISJOINT sets — creation required the path inside the creator's roots
    (own home + open shared roots) while the read side filtered anything
    inside ANY user's home subtree. A shared path created inside the
    creator's own home was therefore unreadable for the rest of the
    tenant, and a fresh deployment could never bootstrap its first clean
    shared root (creation-side anchoring needs one to already exist).
    Round 3 adds a first-class shared namespace ``<base>/shared/<name>``
    (see ``shared_namespace_roots``): creation-side callers include the
    namespace in *creator_roots*; the namespace lies outside every home,
    so the read-side home-subtree filter passes it. Two namespace guards
    apply on BOTH sides:

    - the namespace ROOT itself is not registrable — it is a container,
      not a project (``<base>/shared`` would expose every future sibling
      as a browse root);
    - a namespace that collides with a real user home (an account
      literally named ``shared``) is rejected outright (fail closed):
      the read side would filter everything under it, so creation must
      not accept it.

    Returns an error message when rejected, else ``None``.
    """
    bases = [b for b in (base_dirs or []) if b]
    if not is_valid_path(path, allowed_prefixes=bases):
        return "must be inside a workspace base directory (" + ", ".join(bases) + ")"
    resolved = os.path.realpath(path)
    for base in bases:
        if resolved == os.path.realpath(base):
            return "must not be a workspace base directory itself"
    namespace_roots = set(shared_namespace_roots(bases))
    if resolved in namespace_roots:
        return (
            "must be a named subdirectory of the shared namespace "
            f"(<base>/{SHARED_NAMESPACE_DIRNAME}/<name>), not the namespace root itself"
        )
    creator_root_set = (
        {os.path.realpath(r) for r in creator_roots if r} if creator_roots is not None else set()
    )
    for home in home_dirs or []:
        if not home:
            continue
        resolved_home = os.path.realpath(home)
        if resolved_home == resolved or resolved_home.startswith(resolved + os.sep):
            return "must not be a user home directory or one of its ancestors"
        if resolved_home in namespace_roots and resolved.startswith(resolved_home + os.sep):
            return (
                "the shared namespace collides with the home directory of a user "
                f"account named '{SHARED_NAMESPACE_DIRNAME}'; contact an administrator"
            )
        if resolved.startswith(resolved_home + os.sep) and resolved_home not in creator_root_set:
            return "must not be inside any user's home directory subtree"
    if creator_roots is not None and not any(
        resolved == root or resolved.startswith(root + os.sep) for root in creator_root_set
    ):
        return "shared project path must be inside your own workspace roots"
    return None
