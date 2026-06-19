"""Unit tests for workspace base-dir resolution and path validation (app/routes/fs.py).

Guards the macOS directory-browser bug: when ``WORKSPACE_BASE_DIR`` is unset,
the allowed-prefix default must be the user's actual home directory (e.g.
``/Users/<user>`` on macOS), not the Linux-only ``/home`` — otherwise every
explicit path is rejected with "Path must be under one of: /home".
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.routes.fs import get_workspace_base_dir, get_workspace_base_dirs, is_valid_path


class TestWorkspaceBaseDirDefault:
    """WORKSPACE_BASE_DIR unset → default to the user's home directory."""

    def test_unset_env_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
        assert get_workspace_base_dir() == str(Path.home())

    def test_unset_env_dirs_contain_home(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
        assert str(Path.home()) in get_workspace_base_dirs()

    def test_explicit_env_overrides_home(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/workspace")
        assert get_workspace_base_dir() == "/workspace"
        assert get_workspace_base_dirs() == ["/workspace"]

    def test_comma_separated_multiple_dirs(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/workspace,/opt/projects")
        assert get_workspace_base_dirs() == ["/workspace", "/opt/projects"]

    def test_empty_env_falls_back_to_home(self, monkeypatch):
        # An empty string env value should also fall back (the `or` guard).
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "")
        assert get_workspace_base_dir() == str(Path.home())


class TestIsValidPath:
    """is_valid_path prefix + blacklist + traversal behavior.

    NOTE: tests deliberately avoid pytest's ``tmp_path`` — on Linux it lives
    under /tmp, which is in BLACKLISTED_PATHS, so any tmp_path-based assertion
    would be rejected by the blacklist regardless of the prefix check. We also
    avoid hard-coding /home/... because macOS autofs rewrites /home under
    realpath. Instead we pick a prefix that realpath leaves untouched on both
    platforms by using a workspace env override with a non-blacklisted root.
    """

    def test_home_prefix_allowed_when_default(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
        # A path under the real home dir passes the prefix check (path need not exist).
        candidate = str(Path.home() / "some_project")
        assert is_valid_path(candidate, allowed_prefixes=get_workspace_base_dirs())

    def test_blacklisted_dir_rejected(self):
        # Blacklist fires independent of allowed_prefixes. /usr/bin resolves to
        # itself on both Linux and macOS and is blacklisted on both.
        assert not is_valid_path("/usr/bin", allowed_prefixes=[str(Path.home())])

    def test_inside_prefix_accepted_outside_rejected(self, monkeypatch):
        # Use a synthetic, non-blacklisted prefix that realpath leaves alone.
        # /workspace is not in BLACKLISTED_PATHS and resolves to itself on both
        # Linux and macOS (no autofs/symlink interference).
        prefix = "/workspace"
        assert is_valid_path("/workspace/repo", allowed_prefixes=[prefix])
        # Outside the prefix:
        assert not is_valid_path("/srv/repo", allowed_prefixes=[prefix])

    def test_prefix_boundary_not_prefix_match(self):
        # A sibling whose name extends the prefix must NOT match it.
        prefix = "/workspace"
        assert not is_valid_path("/workspace_evil/repo", allowed_prefixes=[prefix])

    def test_parent_traversal_rejected(self):
        # ".." anywhere in the input is rejected outright (before prefix check).
        assert not is_valid_path("/workspace/repo/../etc", allowed_prefixes=["/workspace"])

    def test_empty_rejected(self):
        assert not is_valid_path("", allowed_prefixes=None)

    def test_no_prefix_restriction_allows_non_blacklisted(self):
        # allowed_prefixes=None → only the blacklist applies.
        assert is_valid_path("/workspace/repo", allowed_prefixes=None)
        assert not is_valid_path("/usr/bin", allowed_prefixes=None)


class TestAdminWorkspaceBaseDirDefault:
    """The duplicate get_workspace_base_dir in admin.py shares the default."""

    def test_admin_unset_env_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
        from app.routes.admin import get_workspace_base_dir as admin_get

        assert admin_get() == str(Path.home())

    def test_admin_explicit_env_overrides(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_BASE_DIR", "/workspace")
        from app.routes.admin import get_workspace_base_dir as admin_get

        assert admin_get() == "/workspace"
