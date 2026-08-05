#!/usr/bin/env python3
"""Route tests for /api/fs/check-path and /api/fs/create-directory.

Focuses on the fix for issue #2317: multi-level new paths should be
validated and created the same way as single-level paths (mkdir -p
semantics).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ---------------------------------------------------------------------------
# Pre-load app.routes.fs directly from its file, bypassing the package
# __init__.py (same pattern as test_fs_file_ops.py).
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402

# Stub Unix-only modules for Windows compatibility
if os.name == "nt":
    for _mod_name in ("pwd", "grp"):
        if _mod_name not in sys.modules:
            _stub = type(sys)(_mod_name)
            _stub.getpwnam = lambda n: type("u", (), {"pw_uid": 0, "pw_gid": 0, "pw_name": n, "pw_dir": "/"})()
            _stub.getpwuid = lambda u: type("u", (), {"pw_uid": u, "pw_gid": 0, "pw_name": "root", "pw_dir": "/"})()
            _stub.getgrnam = lambda n: type("g", (), {"gr_gid": 0, "gr_name": n})()
            _stub.getgrgid = lambda g: type("g", (), {"gr_gid": g, "gr_name": "root"})()
            sys.modules[_mod_name] = _stub

if "app.routes.fs" not in sys.modules:
    for _pkg in [
        "app",
        "app.routes",
        "app.repositories",
        "app.repositories.user_repo",
        "app.utils",
        "app.utils.workspace",
        "app.auth",
        "app.auth.decorators",
        "app.services",
        "app.services.webui_manager",
    ]:
        if _pkg not in sys.modules:
            sys.modules[_pkg] = type(sys)(_pkg)
            if "." not in _pkg[len("app"):] or _pkg.count(".") <= 1:
                sys.modules[_pkg].__path__ = []  # type: ignore[attr-defined]

    class _UR:
        def get_user_by_id(self, _):
            return None

    sys.modules["app.repositories.user_repo"].UserRepository = _UR

    _ad = sys.modules["app.auth.decorators"]
    _ad._extract_token = lambda: None  # type: ignore[attr-defined]
    _ad._load_user_from_token = lambda t: None  # type: ignore[attr-defined]
    _ad.enforce_password_change_requirement = lambda u: None  # type: ignore[attr-defined]

    sys.modules["app.services.webui_manager"].get_webui_manager = lambda: None  # type: ignore[attr-defined]

    _cache_mod = type(sys)("app.utils.cache")

    class _Cache:
        def clear(self):
            pass

    _cache_mod.get_cache = lambda: _Cache()  # type: ignore[attr-defined]
    sys.modules["app.utils.cache"] = _cache_mod
    _auth_svc = type(sys)("app.services.auth_service")
    _auth_svc._security_settings_cache = set()  # type: ignore[attr-defined]
    sys.modules["app.services.auth_service"] = _auth_svc

    _ws = sys.modules["app.utils.workspace"]
    _rspec = importlib.util.spec_from_file_location(
        "_real_workspace_for_test_path_val", str(Path(project_root) / "app/utils/workspace.py")
    )
    _rw = importlib.util.module_from_spec(_rspec)
    _rspec.loader.exec_module(_rw)
    _ws.get_workspace_base_dir = _rw.get_workspace_base_dir
    _ws.get_workspace_base_dirs = _rw.get_workspace_base_dirs
    _ws.OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
    _ws.OPENACE_RM_WRAPPER = _rw.OPENACE_RM_WRAPPER
    _ws.OPENACE_WRITE_AS_WRAPPER = _rw.OPENACE_WRITE_AS_WRAPPER
    _ws._is_wrapper_available = lambda p: False  # type: ignore[attr-defined]
    _ws.run_as_root_if_needed = lambda cmd: None  # type: ignore[attr-defined]

    _fs_spec = importlib.util.spec_from_file_location(
        "app.routes.fs", str(Path(project_root) / "app/routes/fs.py")
    )
    assert _fs_spec is not None and _fs_spec.loader is not None
    _fs_mod = importlib.util.module_from_spec(_fs_spec)
    sys.modules["app.routes.fs"] = _fs_mod
    _fs_spec.loader.exec_module(_fs_mod)


@pytest.fixture
def workspace(tmp_path):
    """A throwaway workspace dir.

    Uses tmp_path on Windows (no blacklist issue). On macOS tmp_path
    resolves under /private/var (blacklisted by is_valid_path), so we
    fall back to a home-relative dir there.
    """
    if os.name == "nt":
        ws = tmp_path / "ws"
        ws.mkdir()
        yield ws
    else:
        ws = Path.home() / ".ace_fs_test_path_val"
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        yield ws
        shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def app(workspace):
    from flask import Flask, g

    from app.routes.fs import fs_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")
    app.before_request_funcs["fs"] = []

    @app.before_request
    def _set_user():
        g.user = {"id": 1, "username": "testuser"}

    with (
        patch("app.routes.fs.get_workspace_base_dir", return_value=str(workspace)),
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(workspace)]),
    ):
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# check-path tests
# ---------------------------------------------------------------------------

class TestCheckPath:
    """Tests for /api/fs/check-path."""

    def test_single_level_new_path_valid(self, client, workspace):
        """Single-level new path should be valid and creatable (regression)."""
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(workspace / "new-project")},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["exists"] is False
        assert data["canCreate"] is True

    def test_multi_level_new_path_valid(self, client, workspace):
        """Multi-level new path should be valid and creatable (issue #2317 fix)."""
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(workspace / "subdir" / "new-project")},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["exists"] is False
        assert data["canCreate"] is True

    def test_deep_multi_level_new_path_valid(self, client, workspace):
        """Deeply nested new path should also be valid."""
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(workspace / "a" / "b" / "c" / "d" / "new-project")},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["exists"] is False
        assert data["canCreate"] is True

    def test_existing_directory_valid(self, client, workspace):
        """Already existing directory should be valid."""
        existing = workspace / "existing-dir"
        existing.mkdir()
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(existing)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["exists"] is True

    def test_existing_file_invalid(self, client, workspace):
        """Path pointing to a file should be invalid."""
        existing_file = workspace / "file.txt"
        existing_file.write_text("hello")
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(existing_file)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is False
        assert data["exists"] is True

    def test_path_outside_workspace_rejected(self, client, workspace):
        """Path outside workspace base dirs should be rejected."""
        resp = client.post(
            "/api/fs/check-path",
            json={"path": "/etc/some/random/path"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["valid"] is False

    def test_path_traversal_rejected(self, client, workspace):
        """Path traversal should be rejected (regression)."""
        resp = client.post(
            "/api/fs/check-path",
            json={"path": str(workspace / ".." / "etc")},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["valid"] is False


# ---------------------------------------------------------------------------
# create-directory tests
# ---------------------------------------------------------------------------

class TestCreateDirectory:
    """Tests for /api/fs/create-directory."""

    def test_create_single_level(self, client, workspace):
        """Single-level directory creation should succeed (regression)."""
        target = workspace / "new-dir"
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": str(target)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert target.is_dir()

    def test_create_multi_level(self, client, workspace):
        """Multi-level directory creation should succeed (issue #2317 fix)."""
        target = workspace / "subdir" / "new-project"
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": str(target)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert target.is_dir()

    def test_create_deep_multi_level(self, client, workspace):
        """Deeply nested directory creation should succeed."""
        target = workspace / "a" / "b" / "c" / "d" / "new-project"
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": str(target)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert target.is_dir()

    def test_create_already_exists(self, client, workspace):
        """Creating an already existing directory should return success."""
        existing = workspace / "existing-dir"
        existing.mkdir()
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": str(existing)},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_create_outside_workspace_rejected(self, client, workspace):
        """Creating outside workspace should be rejected."""
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": "/etc/random-dir"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_create_traversal_rejected(self, client, workspace):
        """Path traversal should be rejected (regression)."""
        resp = client.post(
            "/api/fs/create-directory",
            json={"path": str(workspace / ".." / "etc")},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# find_writable_ancestor unit tests
# ---------------------------------------------------------------------------

class TestFindWritableAncestor:
    """Unit tests for the find_writable_ancestor helper."""

    def test_finds_immediate_parent(self, workspace):
        """When the immediate parent exists, it should be found."""
        from app.routes.fs import find_writable_ancestor

        result = find_writable_ancestor(
            str(workspace / "new-dir"),
            [str(workspace)],
        )
        assert result["found"] is True
        assert result["ancestor"] == str(workspace)

    def test_finds_grandparent(self, workspace):
        """When the parent doesn't exist but grandparent does, it should be found."""
        from app.routes.fs import find_writable_ancestor

        result = find_writable_ancestor(
            str(workspace / "missing" / "new-dir"),
            [str(workspace)],
        )
        assert result["found"] is True
        assert result["ancestor"] == str(workspace)

    def test_finds_deep_ancestor(self, workspace):
        """Should find the nearest existing ancestor in a deep path."""
        from app.routes.fs import find_writable_ancestor

        result = find_writable_ancestor(
            str(workspace / "a" / "b" / "c" / "d" / "new-dir"),
            [str(workspace)],
        )
        assert result["found"] is True
        assert result["ancestor"] == str(workspace)

    def test_outside_allowed_prefixes(self, workspace):
        """Should return error when walking above all allowed prefixes."""
        from app.routes.fs import find_writable_ancestor

        result = find_writable_ancestor(
            "/nonexistent/a/b/c",
            [str(workspace)],
        )
        assert result["found"] is False
        assert "allowed workspace" in result["error"]

    def test_ancestor_not_directory(self, workspace):
        """Should return error when ancestor exists but is not a directory."""
        from app.routes.fs import find_writable_ancestor

        # Create a file that will be an "ancestor" in the path
        file_blocker = workspace / "blocker"
        file_blocker.write_text("not a dir")

        result = find_writable_ancestor(
            str(file_blocker / "sub" / "new-dir"),
            [str(workspace)],
        )
        assert result["found"] is False
        assert "not a directory" in result["error"]

    def test_ancestor_not_writable(self, workspace):
        """Should return error when ancestor exists but is not writable."""
        from unittest.mock import patch as _patch
        from app.routes.fs import find_writable_ancestor

        ro_dir = workspace / "readonly"
        ro_dir.mkdir()

        # Mock get_directory_info to simulate a read-only ancestor
        def mock_get_info(path, system_account=None):
            if str(path) == str(ro_dir):
                return {"exists": True, "is_dir": True, "is_writable": False}
            return {"exists": False, "is_dir": False, "is_writable": False}

        with _patch("app.routes.fs.get_directory_info", side_effect=mock_get_info):
            result = find_writable_ancestor(
                str(ro_dir / "sub" / "new-dir"),
                [str(workspace)],
            )
        assert result["found"] is False
        assert "not writable" in result["error"]

    def test_no_ancestor_found_at_root(self, workspace):
        """Should return error when no existing ancestor is found (root fallback)."""
        from app.routes.fs import find_writable_ancestor

        # Use a path where the allowed prefix itself does not exist
        # and the path is constructed so walking up reaches the prefix
        # boundary without finding any existing ancestor
        result = find_writable_ancestor(
            str(workspace / "x" / "y" / "z"),
            [str(workspace)],
        )
        # workspace exists, so this should find it
        assert result["found"] is True

        # But if we use a prefix that doesn't exist at all
        result = find_writable_ancestor(
            "/nonexistent_root/a/b",
            ["/nonexistent_root"],
        )
        assert result["found"] is False
        assert result["ancestor"] is None
