#!/usr/bin/env python3
"""Route tests for the recursive name-search endpoint (Issue #1923).

Mirrors the stubbing pattern in test_fs_file_ops.py: pre-stub app.* packages
so fs.py can be loaded without triggering the full app/__init__.py import
chain, then register only fs_bp against an isolated Flask app.
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
# Same module pre-stubbing as test_fs_file_ops.py (idempotent — harmless if
# test_fs_file_ops.py already ran and stubbed everything).
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402

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
            if "." not in _pkg[len("app") :] or _pkg.count(".") <= 1:
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
        "_real_workspace_for_search_test", str(Path(project_root) / "app/utils/workspace.py")
    )
    _rw = importlib.util.module_from_spec(_rspec)
    _rspec.loader.exec_module(_rw)
    _ws.get_workspace_base_dir = _rw.get_workspace_base_dir
    _ws.get_workspace_base_dirs = _rw.get_workspace_base_dirs
    _ws.OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
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
def workspace(tmp_path_factory):
    """A throwaway workspace dir under a non-blacklisted path.

    is_valid_path blacklists /root, /tmp, /var, etc. When tests run as root
    (HOME=/root), Path.home() is also blacklisted, so we use the project
    directory itself (under /tools, non-blacklisted) as the workspace parent.
    """
    ws = Path(project_root) / ".test-ws-search"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    user_home = ws / "testuser"
    user_home.mkdir(parents=True, exist_ok=True)
    yield ws, user_home
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def app(workspace):
    from flask import Flask, g

    from app.routes.fs import fs_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")
    app.before_request_funcs["fs"] = []

    ws_root, user_home = workspace

    @app.before_request
    def _set_user():
        g.user = {"id": 1, "username": "testuser"}

    with (
        patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
        patch("app.routes.fs.get_home_directory", return_value=str(user_home)),
    ):
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _build_tree(user_home: Path):
    """Create a nested fixture tree:

    user_home/
      report-2025.txt
      report-2024.txt
      notes.md
      .secret              # hidden, must be excluded
      projects/
        alpha-report.md
        beta/
          deep-report.txt
          photo.png
        .hidden-dir/       # hidden dir, must be excluded (and not descended)
          leaked.txt
    """
    (user_home / "report-2025.txt").write_text("a")
    (user_home / "report-2024.txt").write_text("b")
    (user_home / "notes.md").write_text("c")
    (user_home / ".secret").write_text("d")
    projects = user_home / "projects"
    projects.mkdir()
    (projects / "alpha-report.md").write_text("e")
    beta = projects / "beta"
    beta.mkdir()
    (beta / "deep-report.txt").write_text("f")
    (beta / "photo.png").write_text("g")
    hidden_dir = projects / ".hidden-dir"
    hidden_dir.mkdir()
    (hidden_dir / "leaked.txt").write_text("h")


class TestNameMatcher:
    def test_single_token_case_insensitive(self):
        from app.routes.fs import _build_name_matcher

        m = _build_name_matcher("Report")
        assert m("report-2025.txt")
        assert m("weekly-REPORT.md")
        assert not m("notes.md")

    def test_multi_keyword_and(self):
        from app.routes.fs import _build_name_matcher

        m = _build_name_matcher("report 2025")
        assert m("report-2025.txt")
        assert m("2025-report-final.md")
        assert not m("report-2024.txt")  # missing 2025
        assert not m("notes-2025.md")  # missing report

    def test_empty_query_returns_none(self):
        from app.routes.fs import _build_name_matcher

        assert _build_name_matcher("") is None
        assert _build_name_matcher("   ") is None


class TestSearchEndpoint:
    def test_search_finds_files_recursively(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=report")
        assert resp.status_code == 200
        data = resp.get_json()
        names = {r["name"] for r in data["results"]}
        # All four report files across two levels, plus zero dirs match "report".
        assert names == {"report-2025.txt", "report-2024.txt", "alpha-report.md", "deep-report.txt"}
        assert data["total"] == 4
        assert data["truncated"] is False

    def test_search_includes_directories(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=projects")
        data = resp.get_json()
        types = {r["type"] for r in data["results"]}
        assert types == {"dir"}
        names = {r["name"] for r in data["results"]}
        assert "projects" in names

    def test_search_multi_keyword_and(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=report 2025")
        data = resp.get_json()
        names = {r["name"] for r in data["results"]}
        assert names == {"report-2025.txt"}

    def test_search_case_insensitive(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=REPORT")
        data = resp.get_json()
        assert len(data["results"]) == 4

    def test_search_excludes_hidden(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        # "secret" would match .secret, "leaked" would match the file inside
        # .hidden-dir — both must be excluded.
        resp = client.get("/api/fs/search?q=secret")
        data = resp.get_json()
        assert data["total"] == 0

        resp = client.get("/api/fs/search?q=leaked")
        data = resp.get_json()
        assert data["total"] == 0

    def test_search_empty_query_returns_400(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=")
        assert resp.status_code == 400

        resp = client.get("/api/fs/search?q=   ")
        assert resp.status_code == 400

    def test_search_path_outside_home_rejected(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        # /etc is blacklisted; is_valid_path rejects it.
        resp = client.get("/api/fs/search?q=report&path=/etc")
        assert resp.status_code == 400

    def test_search_relative_path_is_correct(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=deep-report")
        data = resp.get_json()
        assert data["total"] == 1
        entry = data["results"][0]
        assert entry["relative_path"] == os.path.join("projects", "beta", "deep-report.txt")
        assert entry["path"] == str(user_home / "projects" / "beta" / "deep-report.txt")

    def test_search_max_depth_limits_recursion(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        # max_depth=1 → only direct children of home.
        resp = client.get("/api/fs/search?q=report&max_depth=1")
        data = resp.get_json()
        names = {r["name"] for r in data["results"]}
        assert names == {"report-2025.txt", "report-2024.txt"}

        # max_depth=2 → home + one level down (projects/alpha-report.md).
        resp = client.get("/api/fs/search?q=report&max_depth=2")
        data = resp.get_json()
        names = {r["name"] for r in data["results"]}
        assert "alpha-report.md" in names
        # deep-report.txt is at depth 3, excluded.
        assert "deep-report.txt" not in names

    def test_search_max_results_truncates(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=report&max_results=2")
        data = resp.get_json()
        assert data["total"] == 2
        assert data["truncated"] is True

    def test_search_kind_filter_dir(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=report&kind=dir")
        data = resp.get_json()
        assert all(r["type"] == "dir" for r in data["results"])
        # No directory name contains "report" in the fixture tree.
        assert data["total"] == 0

    def test_search_kind_filter_file(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=projects&kind=file")
        data = resp.get_json()
        assert all(r["type"] == "file" for r in data["results"])
        assert data["total"] == 0

    def test_search_uses_current_path_as_root(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        # Search from the projects subdirectory: only alpha-report + beta/*.
        resp = client.get(f"/api/fs/search?q=report&path={user_home / 'projects'}")
        data = resp.get_json()
        names = {r["name"] for r in data["results"]}
        assert names == {"alpha-report.md", "deep-report.txt"}

    def test_search_result_file_has_size(self, client, workspace):
        _, user_home = workspace
        _build_tree(user_home)

        resp = client.get("/api/fs/search?q=notes")
        data = resp.get_json()
        assert data["total"] == 1
        entry = data["results"][0]
        assert entry["type"] == "file"
        assert entry["size"] == len("c")
        assert entry["is_readable"] is True
