"""Unit tests for shared-project path validation in api_create_project.

Issue #3376 review round 1, item 1: a shared project's path extends every
tenant member's fs browse roots (fs._allowed_roots_for_user). These tests
pin the create-side rejection; the read-side filter is covered in
test_fs_home_lock_3376.py.

Review round 2 (3994613216): round 1 only rejected homes and their
ancestors — descendants of a foreign home (<base>/alice/.ssh) passed.
Creation now also enforces creator ownership: the path must land inside
the creator's own home roots or a shared root already open to the tenant.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3376)]

USER_B = {"id": 9, "user_id": 9, "username": "bob", "role": "user", "tenant_id": 1}

# alice + bob have homes under the workspace base dir.
_USER_ROWS = [
    {"id": 7, "username": "alice", "system_account": None},
    {"id": 9, "username": "bob", "system_account": None},
]


@pytest.fixture
def workspace():
    ws = Path.home() / ".ace_proj_shared_test_3376"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    (ws / "alice").mkdir(parents=True)
    (ws / "bob").mkdir()
    yield ws
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def projects_app(workspace):
    from app.routes.projects import projects_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_bp, url_prefix="/api")

    # projects_bp 是模块级单例:app 级清空蓝图回调后注入 g.user
    app.before_request_funcs["projects"] = []

    @app.before_request
    def _set_user():
        g.user = dict(USER_B)
        g.user_id = USER_B["id"]
        g.user_role = USER_B["role"]
        g.tenant_id = USER_B["tenant_id"]
        return None

    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects.get_workspace_base_dirs", return_value=[str(workspace)]),
        patch("app.routes.projects.user_repo.get_all_users", return_value=list(_USER_ROWS)),
        # review round 2: 创建者"已开放的共享根"来自 repo;默认无共享根
        # (个别用例在内部再整体 patch project_repo 提供共享根)
        patch("app.routes.projects.project_repo.get_shared_project_paths", return_value=[]),
    ):
        yield app


def _create(client, path, is_shared=True):
    return client.post(
        "/api/projects",
        json={"path": path, "name": "p", "is_shared": is_shared, "create_dir": False},
    )


def test_shared_workspace_base_dir_rejected(projects_app, workspace):
    """攻击链第一步:POST {"path": "<base>", "is_shared": true} → 400。"""
    client = projects_app.test_client()
    resp = _create(client, str(workspace))
    assert resp.status_code == 400
    assert "shared project path" in resp.get_json()["error"].lower()


def test_shared_foreign_home_rejected(projects_app, workspace):
    """他人 home(alice)不得注册为共享项目。"""
    client = projects_app.test_client()
    resp = _create(client, str(workspace / "alice"))
    assert resp.status_code == 400


def test_shared_own_home_rejected(projects_app, workspace):
    """自己的 home 也不行(含自己:共享根=home 会放大成整棵子树枚举)。"""
    client = projects_app.test_client()
    resp = _create(client, str(workspace / "bob"))
    assert resp.status_code == 400


def test_shared_outside_base_dir_rejected(projects_app, workspace):
    client = projects_app.test_client()
    resp = _create(client, "/etc/team")
    assert resp.status_code == 400


def test_shared_ancestor_of_home_rejected(projects_app, workspace):
    # base dir 的父目录在 prefix 检查即被拒(不在任何 base 下)
    client = projects_app.test_client()
    resp = _create(client, str(workspace.parent))
    assert resp.status_code == 400


# --- review round 2 [3994613216]: home descendants + creator ownership ----


def test_shared_foreign_home_descendant_rejected(projects_app, workspace):
    """bob 注册 /workspace/alice/.ssh、/workspace/alice/secrets → 400。

    Review round 2:home 后代(任意深度)不再放行,堵住"拿他人 home 子树
    当共享根"的攻击链第二步。
    """
    client = projects_app.test_client()
    for sub in (".ssh", "secrets", "deep/nested"):
        resp = _create(client, str(workspace / "alice" / sub))
        assert resp.status_code == 400, sub
        assert "shared project path" in resp.get_json()["error"].lower()


def test_shared_own_home_subpath_accepted(projects_app, workspace):
    """bob 注册自己 home 内子路径 → 通过拓扑校验(归属规则允许)。"""
    client = projects_app.test_client()
    with patch("app.routes.projects.project_repo") as repo:
        repo.get_project_by_path.return_value = None
        repo.create_project.return_value = 42
        repo.get_project_by_id.return_value = None  # 404 分支即视为创建已放行
        resp = _create(client, str(workspace / "bob" / "team-proj"))
    assert resp.status_code in (200, 201, 404)  # 不是 400 校验拒绝
    assert repo.create_project.called


def test_shared_first_level_non_home_requires_ownership(projects_app, workspace):
    """<base>/team-proj 不在创建者任何根内 → 400(归属规则)。

    Review round 2:round 1 允许任意用户把 base 下的一级团队目录注册为
    共享项目;现在必须在创建者自己的根(home 或已开放共享根)之内。
    """
    client = projects_app.test_client()
    resp = _create(client, str(workspace / "team-proj"))
    assert resp.status_code == 400
    assert "own workspace roots" in resp.get_json()["error"]


def test_shared_inside_open_shared_root_accepted(projects_app, workspace):
    """已对其开放的共享根内的子路径仍可注册(嵌套共享)。"""
    client = projects_app.test_client()
    with patch("app.routes.projects.project_repo") as repo:
        repo.get_shared_project_paths.return_value = [str(workspace / "team-area")]
        repo.get_project_by_path.return_value = None
        repo.create_project.return_value = 45
        repo.get_project_by_id.return_value = None
        resp = _create(client, str(workspace / "team-area" / "sub"))
    assert resp.status_code in (200, 201, 404)
    assert repo.create_project.called


@pytest.mark.regression
def test_non_shared_project_not_subject_to_topology_rule(projects_app, workspace):
    """非共享项目不放大任何人的根,维持既有任意绝对路径口径。"""
    client = projects_app.test_client()
    with patch("app.routes.projects.project_repo") as repo:
        repo.get_project_by_path.return_value = None
        repo.create_project.return_value = 43
        repo.get_project_by_id.return_value = None
        resp = _create(client, str(workspace), is_shared=False)
    assert resp.status_code in (200, 201, 404)
    assert repo.create_project.called
