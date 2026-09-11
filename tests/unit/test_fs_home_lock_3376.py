"""Unit tests for fs browse/check-path home subtree lock (Issue #3376)."""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3376)]

USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
_NO_TENANT_USER = {"id": 8, "user_id": 8, "username": "bob", "role": "user", "tenant_id": None}
_CURRENT_USER: dict = {}


def _switch_user(user):
    _CURRENT_USER.clear()
    _CURRENT_USER.update(user)


@pytest.fixture
def workspace():
    """Throwaway dirs under the real home (non-blacklisted).

    macOS maps /tmp through /private/tmp (blacklisted) — under /tmp the
    OLD gate would reject everything and the new lock's red/green signal
    would be lost. CI must not run this file as root (/root is
    blacklisted for the same reason).
    """
    ws = Path.home() / ".ace_fs_lock_test_3376"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    home = ws / "home"
    shared = ws / "shared-proj"
    other = ws / "someone-else"
    home.mkdir(parents=True)
    shared.mkdir()
    other.mkdir()
    yield ws, home, shared, other
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def fs_app(workspace):
    from app.routes.fs import fs_bp

    ws, home, shared, other = workspace
    _switch_user(USER)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")

    # 先例 test_fs_file_ops.py:142-155:app 级清空蓝图回调,注入 g.user
    # (fs_bp 是模块级单例,不得改动其回调表以免泄漏到其他测试文件)
    app.before_request_funcs["fs"] = []

    @app.before_request
    def _set_user():
        g.user = dict(_CURRENT_USER)

    with (
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws)]),
        patch("app.routes.fs.get_home_directory", return_value=str(home)),
        patch(
            "app.repositories.project_repo.ProjectRepository.get_shared_project_paths",
            lambda self, tenant_id=None: [str(shared)] if tenant_id == 1 else [],
        ),
        patch(
            "app.routes.fs.get_directory_info",
            lambda path, sa: {
                "exists": os.path.isdir(path),
                "is_dir": True,
                "is_readable": True,
                "is_writable": True,
            },
        ),
        patch(
            "app.routes.fs.list_subdirectories",
            lambda path, sa, include_files=False: {"directories": [], "files": []},
        ),
    ):
        yield app


def _browse(client, path):
    return client.get(f"/api/fs/browse?path={path}")


def test_browse_outside_home_and_shared_rejected(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = _browse(client, str(other))
    assert resp.status_code == 400


@pytest.mark.regression
def test_browse_home_and_shared_allowed(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    assert _browse(client, str(home)).status_code == 200
    assert _browse(client, str(shared)).status_code == 200
    assert _browse(client, "home").status_code == 200


def test_browse_no_tenant_user_home_only(fs_app, workspace):
    # tenant_id 为 None 的用户不放大到全部共享(N10:None -> [])
    ws, home, shared, other = workspace
    _switch_user(_NO_TENANT_USER)
    client = fs_app.test_client()
    assert _browse(client, str(home)).status_code == 200
    resp = _browse(client, str(shared))
    # shared 由 repo stub 按 tenant_id==1 过滤,None 拿不到
    assert resp.status_code == 400


def test_check_path_outside_home_and_shared_rejected(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(other)})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False


@pytest.mark.regression
def test_check_path_shared_root_allowed(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(shared)})
    assert resp.status_code == 200


def test_browse_symlinked_home_allowed(fs_app, workspace):
    # B2:home 根必须 realpath,符号链接 home 不能误拒
    ws, home, shared, other = workspace
    link = ws / "home-link"
    if not link.is_symlink():
        link.symlink_to(home)
    with patch("app.routes.fs.get_home_directory", return_value=str(link)):
        client = fs_app.test_client()
        assert _browse(client, str(home)).status_code == 200
