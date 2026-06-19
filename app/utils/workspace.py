"""Workspace base-directory resolution.

Single source of truth for the ``WORKSPACE_BASE_DIR`` env var so the directory
browser's allowed-prefix logic is consistent across routes (fs / admin /
workspace). When the env var is unset, the default is the current user's home
directory (e.g. ``/Users/<user>`` on macOS, ``/home/<user>`` on Linux) so the
browser works out of the box on any platform; Docker/server deployments set
the env explicitly (e.g. ``/workspace``).
"""

import os
from pathlib import Path

__all__ = ["get_workspace_base_dir", "get_workspace_base_dirs"]


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
