"""Unit tests for app.utils.path_guard (Issue #3376 relocation)."""

import pytest

pytestmark = [pytest.mark.issue(3376)]


def test_is_valid_path_rejects_traversal_and_relative():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/../../etc") is False
    assert is_valid_path("relative/path") is False
    assert is_valid_path("") is False


def test_is_valid_path_rejects_blacklisted_resolved():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/etc/passwd") is False
    assert is_valid_path("/var/log/x") is False


def test_is_valid_path_accepts_normal_absolute():
    from app.utils.path_guard import is_valid_path

    assert is_valid_path("/home/alice/project") is True


def test_is_valid_path_prefix_boundary():
    """Prefix boundary semantics (Issue #3376).

    Uses the real home directory as the prefix: macOS maps /home to an
    autofs symlink, so hardcoded /home/alice paths realpath elsewhere and
    would fail for environment reasons, not boundary reasons.
    """
    from pathlib import Path

    from app.utils.path_guard import is_valid_path

    home = str(Path.home())
    assert is_valid_path(home, allowed_prefixes=[home]) is True
    assert is_valid_path(home + "_evil", allowed_prefixes=[home]) is False


def test_fs_module_reexports_shared_validator():
    """fs.py 保留模块级名字,既有 patch/引用不断(workspace.py:39 等)。"""
    import app.routes.fs as fs_mod
    import app.utils.path_guard as guard_mod

    assert fs_mod.is_valid_path is guard_mod.is_valid_path
