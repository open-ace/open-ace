"""Unit tests for shared-project path validation in api_create_project.

Issue #3376 review round 1, item 1: a shared project's path extends every
tenant member's fs browse roots (fs._allowed_roots_for_user). These tests
pin the create-side rejection; the read-side filter is covered in
test_fs_home_lock_3376.py.

Review round 2 (3994613216): round 1 only rejected homes and their
ancestors — descendants of a foreign home (<base>/alice/.ssh) passed.
Creation now also enforces creator ownership: the path must land inside
the creator's own home roots or a shared root already open to the tenant.

Review round 3 (PR #3380): round 2's creation-side ownership rule and the
read-side home-subtree filter accepted disjoint sets (home-internal paths
were created but never surfaced; fresh deployments could not bootstrap a
first shared root). New shared registrations now go through the
first-class <base>/shared/ namespace; the E2E test below drives the real
ProjectRepository against a real sqlite projects table.
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


def test_shared_own_home_subpath_rejected_with_namespace_hint(projects_app, workspace):
    """bob 注册自己 home 内子路径 → 400,文案指向 <base>/shared/<name>。

    Review round 4:创建侧曾允许"自己 home 内"的共享路径,而读取侧无条件
    丢弃所有 home 子树行——用户会拿到 201 与组共享目录权限,然后共享对
    其他租户成员永远不可见(静默死共享)。home 根已从共享项目的
    creator_roots 移除,拒绝信息指向一等共享命名空间。
    """
    client = projects_app.test_client()
    with patch("app.routes.projects.project_repo") as repo:
        repo.get_project_by_path.return_value = None
        resp = _create(client, str(workspace / "bob" / "team-proj"))
    assert resp.status_code == 400
    error = resp.get_json()["error"].lower()
    assert "shared projects must live under" in error
    assert "shared/<name>" in error
    assert not repo.create_project.called


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


# --- review round 3 (PR #3380): first-class shared namespace ---------------
#
# Round 2's creation-side ownership rule and the read-side home-subtree
# filter accepted DISJOINT sets: paths inside the creator's own home were
# created but filtered for every other tenant member, and a fresh
# deployment could never bootstrap its first clean shared root (anchoring
# requires one to already exist). New registrations now go through the
# <base>/shared/ namespace, which lies outside every user home.


def test_shared_namespace_child_accepted_without_any_anchor(projects_app, workspace):
    """自举用例:<base>/shared/team-proj 无任何既有共享根也可注册。

    Round 2 时该路径会被归属规则拒绝(不在创建者根内);round 3 命名空间
    一等公民化后,默认 fixture(无 open shared roots)直接放行。
    """
    client = projects_app.test_client()
    with patch("app.routes.projects.project_repo") as repo:
        repo.get_project_by_path.return_value = None
        repo.create_project.return_value = 46
        repo.get_project_by_id.return_value = None
        resp = _create(client, str(workspace / "shared" / "team-proj"))
    assert resp.status_code in (200, 201, 404)
    assert repo.create_project.called


def test_shared_namespace_root_itself_rejected(projects_app, workspace):
    """<base>/shared 是容器不是项目:命名空间根本身不可注册。"""
    client = projects_app.test_client()
    resp = _create(client, str(workspace / "shared"))
    assert resp.status_code == 400
    assert "namespace root" in resp.get_json()["error"]


def test_shared_namespace_collision_with_shared_account_rejected(projects_app, workspace):
    """账户名 "shared" 的 home 与命名空间碰撞 → 拒绝并给出明确 message。

    不拒绝会重现 round 2 的死局:创建侧放行、读取侧滤除。fail-closed。
    """
    client = projects_app.test_client()
    collided_rows = list(_USER_ROWS) + [{"id": 11, "username": "shared", "system_account": None}]
    with patch("app.routes.projects.user_repo.get_all_users", return_value=collided_rows):
        resp = _create(client, str(workspace / "shared" / "team-proj"))
    assert resp.status_code == 400
    assert "collides" in resp.get_json()["error"]


# --- review round 3: end-to-end bootstrap over a REAL projects table -------
#
# Reviewer-requested E2E: empty projects table → user A creates a shared
# project through the <base>/shared/ namespace → the path shows up in
# ANOTHER tenant member B's fs._allowed_roots_for_user. The anchoring
# mechanism itself (ProjectRepository SQL against a real sqlite projects
# table + the read-side filter) runs for real — only the database
# location is redirected to a throwaway file.

_PROJECTS_DDL = """
CREATE TABLE projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 path TEXT NOT NULL,
 name TEXT,
 description text,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 is_active INTEGER DEFAULT 1 NOT NULL,
 is_shared INTEGER DEFAULT 0 NOT NULL,
 tenant_id integer DEFAULT 1 NOT NULL,
 permission_status TEXT,
 permission_task_id TEXT
)
"""

_USER_PROJECTS_DDL = """
CREATE TABLE user_projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 project_id integer NOT NULL,
 first_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 last_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 total_sessions integer DEFAULT 0 NOT NULL,
 total_tokens integer DEFAULT 0 NOT NULL,
 total_requests integer DEFAULT 0 NOT NULL,
 total_duration_seconds integer DEFAULT 0 NOT NULL
)
"""


def test_e2e_namespace_bootstrap_visible_to_other_tenant_member(workspace, tmp_path):
    """空 projects 表 → alice 创建 <base>/shared/team-proj → bob 的允许根可见。

    真实 ProjectRepository + 真实 sqlite 表;断言同时覆盖创建侧放行、
    读取侧通过(_shared_root_rejection_reason 为 None)与
    _allowed_roots_for_user 锚定结果。
    """
    from flask import Flask

    import app.repositories.project_repo as pr_mod
    from app.repositories.database import Database
    from app.routes import fs as fs_mod
    from app.routes.projects import projects_bp

    ws = workspace
    real_cls = pr_mod.ProjectRepository
    db = Database(db_url=f"sqlite:///{tmp_path / 'proj-e2e-3376.db'}")
    with db.connection() as conn:
        conn.execute(_PROJECTS_DDL)
        conn.execute(_USER_PROJECTS_DDL)
        conn.commit()
    repo = real_cls(db=db)
    # 前置:真实共享根为空(空表)
    assert repo.get_shared_project_paths(1) == []

    alice = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
    bob = {"id": 9, "user_id": 9, "username": "bob", "role": "user", "tenant_id": 1}
    target = ws / "shared" / "team-proj"

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.before_request_funcs["projects"] = []

    @app.before_request
    def _set_alice():
        g.user = dict(alice)
        g.user_id = alice["id"]
        g.user_role = alice["role"]
        g.tenant_id = alice["tenant_id"]
        return None

    # 用户 A(alice)经创建侧:命名空间路径通过拓扑校验并真实落库
    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects.get_workspace_base_dirs", return_value=[str(ws)]),
        patch("app.routes.projects.user_repo.get_all_users", return_value=list(_USER_ROWS)),
        patch("app.routes.projects.project_repo", repo),
    ):
        resp = app.test_client().post(
            "/api/projects",
            json={"path": str(target), "name": "team", "is_shared": True, "create_dir": False},
        )
    assert resp.status_code == 201, resp.get_json()
    # 行确实写进了真实 projects 表
    assert repo.get_shared_project_paths(1) == [os.path.realpath(str(target))]

    # 用户 B(bob)经读取侧:真实 repo 查询 + 真实过滤器,不桩锚定机制
    with (
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws)]),
        patch("app.routes.fs.user_repo.get_all_users", return_value=list(_USER_ROWS)),
        patch.object(pr_mod, "ProjectRepository", lambda: real_cls(db=db)),
    ):
        # 对偶断言之读取侧:命名空间行不在任何 home 子树内 → 通过
        assert fs_mod._shared_root_rejection_reason(os.path.realpath(str(target))) is None
        # 锚定结果:B 的允许根包含 A 创建的共享路径
        roots = fs_mod._allowed_roots_for_user(dict(bob))
    assert os.path.realpath(str(target)) in roots
    # B 的根集不因共享行放大到他人 home
    assert os.path.realpath(str(ws / "alice")) not in roots


@pytest.mark.parametrize(
    "path",
    [
        "{base}/shared/team-proj",
        "{base}/shared/a/b",
        "{base}/shared/team-proj",
    ],
)
def test_create_side_accepts_exactly_what_read_side_accepts(projects_app, workspace, path):
    """对称不变量(review round 4 点名):创建侧放行的共享路径,读取侧
    滤波器必须同样放行——两侧口径漂移是 round 2(不相交集合)与 round 4
    (创建成功但不可见)两个缺陷的共同根源。"""
    from app.routes.fs import _shared_root_rejection_reason
    from app.utils.path_guard import shared_namespace_roots

    bases = [str(workspace)]
    homes = [str(workspace / "alice"), str(workspace / "bob")]
    resolved = path.format(base=str(workspace))
    # The exact creator_roots the route assembles for a shared registration.
    creator_roots = shared_namespace_roots(bases)
    from app.utils.path_guard import shared_project_path_error

    assert shared_project_path_error(resolved, bases, homes, creator_roots=creator_roots) is None
    # Read side (creator-less) must agree for every create-side-accepted path.
    assert _shared_root_rejection_reason(resolved, home_dirs=homes) is None
