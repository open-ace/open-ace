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
    """is_valid_path prefix + blacklist + traversal behavior."""

    def test_home_prefix_allowed_when_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
        # A path under the real home dir passes the prefix check (path need not exist).
        candidate = str(Path.home() / "some_project")
        assert is_valid_path(candidate, allowed_prefixes=get_workspace_base_dirs())

    def test_blacklisted_dir_rejected(self, monkeypatch, tmp_path):
        # Blacklist fires independent of allowed_prefixes. Use a real tmp dir as
        # the allowed prefix, and a blacklisted path that resolves identically on
        # both Linux (/usr/bin) — note macOS symlinks /etc → /private/etc so /etc
        # is NOT caught there (a pre-existing platform quirk, out of scope here).
        home = str(tmp_path)
        assert not is_valid_path("/usr/bin", allowed_prefixes=[home])

    def test_outside_prefix_rejected(self, tmp_path):
        # A path outside the allowed prefix is rejected even if not blacklisted.
        # Use tmp_path (which exists) so realpath doesn't traverse symlinks.
        inside = tmp_path / "repo"
        outside = tmp_path.parent / "other_user"
        assert is_valid_path(str(inside), allowed_prefixes=[str(tmp_path)])
        assert not is_valid_path(str(outside), allowed_prefixes=[str(tmp_path)])

    def test_prefix_boundary_not_prefix_match(self, tmp_path):
        # tmp_path must not match as a prefix of a sibling whose name extends it,
        # and vice-versa (prevents /home/alice matching /home/alice_evil).
        sibling = tmp_path.parent / (tmp_path.name + "_evil")
        assert not is_valid_path(str(sibling), allowed_prefixes=[str(tmp_path)])

    def test_parent_traversal_rejected(self, tmp_path):
        # ".." anywhere in the input is rejected outright.
        assert not is_valid_path(str(tmp_path / ".." / "etc"), allowed_prefixes=[str(tmp_path)])

    def test_empty_rejected(self):
        assert not is_valid_path("", allowed_prefixes=None)

    def test_no_prefix_restriction_allows_non_blacklisted(self, tmp_path):
        # allowed_prefixes=None → only the blacklist applies. A real tmp path
        # is not blacklisted and resolves to itself.
        assert is_valid_path(str(tmp_path), allowed_prefixes=None)
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
