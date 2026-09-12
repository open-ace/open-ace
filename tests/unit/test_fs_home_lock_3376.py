"""Unit tests for fs browse/check-path home subtree lock (Issue #3376)."""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3376)]

# account = username (no system_account) → home roots are <base>/alice per
# configured workspace base dir (review round 1: plural root resolution).
USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
_NO_TENANT_USER = {"id": 8, "user_id": 8, "username": "bob", "role": "user", "tenant_id": None}
_NO_IDENTITY_USER = {"id": 9, "user_id": 9, "role": "user", "tenant_id": 1}
_CURRENT_USER: dict = {}

# All user rows the (patched) UserRepository reports — alice + bob have
# homes under the workspace base dir; used by the shared-root filter.
_USER_ROWS = [
    {"id": 7, "username": "alice", "system_account": None},
    {"id": 8, "username": "bob", "system_account": None},
]


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
    home = ws / "alice"
    shared = ws / "shared-proj"
    other = ws / "bob"
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
        patch(
            "app.repositories.project_repo.ProjectRepository.get_shared_project_paths",
            lambda self, tenant_id=None: [str(shared)] if tenant_id == 1 else [],
        ),
        patch("app.routes.fs.user_repo.get_all_users", return_value=list(_USER_ROWS)),
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
    assert _browse(client, str(ws / "bob")).status_code == 200
    resp = _browse(client, str(shared))
    # shared 由 repo stub 按 tenant_id==1 过滤,None 拿不到
    assert resp.status_code == 400


def test_check_path_first_level_under_base_validatable(fs_app, workspace):
    """Review round 1 [5] / #2317: check-path admits the base dirs as roots."""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(ws / "not-a-home-x")})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True


def test_check_path_outside_workspace_rejected(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    # /etc-style path fails is_valid_path prefix check well before the lock
    resp = client.post("/api/fs/check-path", json={"path": "/etc/random/dir"})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False


@pytest.mark.regression
def test_check_path_shared_root_allowed(fs_app, workspace):
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(shared)})
    assert resp.status_code == 200


def test_browse_symlinked_home_allowed(fs_app, workspace):
    # B2:home 根必须 realpath,符号链接 base dir 不能误拒(review round 1:
    # 复数根解析对每个 <base>/<account> realpath)
    ws, home, shared, other = workspace
    real_base = ws / "real-base"
    real_base.mkdir()
    (real_base / "alice").mkdir()
    link = ws / "base-link"
    if not link.is_symlink():
        link.symlink_to(real_base)
    with patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws), str(link)]):
        client = fs_app.test_client()
        # 通过符号链接 base 拼出的 home 根 realpath 到 real-base/alice
        assert _browse(client, str(real_base / "alice")).status_code == 200
        assert _browse(client, str(link / "alice")).status_code == 200


# --- review round 1: [5] check-path admits base dirs, browse does not ---


@pytest.mark.regression
def test_check_path_base_dir_validatable_but_browse_locked(fs_app, workspace):
    """#2317 flow: base dir is check-path valid; browse still refuses it."""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(ws / "new-project")})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True

    # the base dir itself is validatable ("可校验不可枚举")...
    resp = client.post("/api/fs/check-path", json={"path": str(ws)})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True

    # ...but browse keeps the home subtree lock (no enumeration).
    resp = _browse(client, str(ws))
    assert resp.status_code == 400
    resp = _browse(client, str(ws / "new-project"))
    assert resp.status_code == 400


# --- review round 1: [8] plural workspace base dirs / identity fallback ---


def test_multi_root_workspace_base_dir_homes(fs_app, workspace):
    """WORKSPACE_BASE_DIR=/a,/b → home roots /a/alice AND /b/alice."""
    ws, home, shared, other = workspace
    base2 = ws / "base2"
    base2.mkdir()
    (base2 / "alice").mkdir()
    (base2 / "bob").mkdir()
    with patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws), str(base2)]):
        client = fs_app.test_client()
        assert _browse(client, str(ws / "alice")).status_code == 200
        assert _browse(client, str(base2 / "alice")).status_code == 200
        # bob 的 home 依然不可见
        assert _browse(client, str(base2 / "bob")).status_code == 400


def test_identityless_user_browse_rejected_not_process_home(fs_app, workspace):
    """无 system_account 也无 username → 空根列表,绝不落回进程 home。"""
    ws, home, shared, other = workspace
    _switch_user(_NO_IDENTITY_USER)
    client = fs_app.test_client()
    # default "home" browse is rejected outright
    resp = _browse(client, "home")
    assert resp.status_code == 400
    assert "process" not in resp.get_json().get("error", "").lower()
    # explicit paths are rejected too (no root contains them)
    assert _browse(client, str(home)).status_code == 400
    assert _browse(client, str(Path.home())).status_code == 400


# --- review round 1: [1] shared-project root self-expansion defense ---


def _with_shared_paths(fs_app, workspace, paths):
    """Point the repo stub at attacker-chosen shared paths."""
    return patch(
        "app.repositories.project_repo.ProjectRepository.get_shared_project_paths",
        lambda self, tenant_id=None: list(paths) if tenant_id == 1 else [],
    )


@pytest.mark.regression
def test_attack_chain_shared_base_dir_does_not_unlock_other_homes(fs_app, workspace):
    """B 把 workspace 根注册为共享项目 → 他人 home 仍被拒。

    复现 #3376 review round 1 item 1:租户成员 POST /api/projects
    {"path": "/workspace", "is_shared": true} 后 browse 他人 home。
    api_create_project 现在拒绝该请求;此处验证读侧纵深防御(历史脏行)
    同样把它滤掉。
    """
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    with _with_shared_paths(fs_app, workspace, [str(ws)]):
        # the base dir itself stays browsable-NO: it is filtered as a root
        assert _browse(client, str(ws)).status_code == 400
        # ...and so does every home beneath it
        assert _browse(client, str(other)).status_code == 400
        assert _browse(client, str(home)).status_code == 200  # own home unaffected


@pytest.mark.regression
def test_attack_chain_shared_foreign_home_rejected(fs_app, workspace):
    """B 把他人的 home 注册为共享项目 → 该 home 不进入允许根。"""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    with _with_shared_paths(fs_app, workspace, [str(other), str(shared)]):
        assert _browse(client, str(other)).status_code == 400
        # legit deep shared project still works
        assert _browse(client, str(shared)).status_code == 200


def test_shared_first_level_non_home_path_still_allowed(fs_app, workspace):
    """base dir 直系一级下不是任何用户 home 的路径(如 team-proj)仍可共享。"""
    ws, home, shared, other = workspace
    team = ws / "team-proj"
    team.mkdir(exist_ok=True)
    client = fs_app.test_client()
    with _with_shared_paths(fs_app, workspace, [str(team)]):
        assert _browse(client, str(team)).status_code == 200


# --- review round 2 [3994613216]: home descendants filtered at ANY depth ---


@pytest.mark.regression
def test_attack_chain_shared_foreign_home_descendants_rejected(fs_app, workspace):
    """repo 脏行返回他人 home 后代路径(/ws/alice/.ssh 等)→ 被滤除,browse 不可达。

    Review round 2:round 1 的读取侧只挡"home 本身/祖先",后代子树漏过;
    现在 home 子树(任意深度)一律不进入允许根。以 bob(攻击者)视角验证:
    alice 本人仍可经自己的 home 根访问,但共享机制不得把它放大给 bob。
    """
    ws, home, shared, other = workspace
    (home / ".ssh").mkdir()
    (home / "secrets").mkdir()
    _switch_user({"id": 10, "user_id": 10, "username": "bob", "role": "user", "tenant_id": 1})
    client = fs_app.test_client()
    with _with_shared_paths(
        fs_app, workspace, [str(home / ".ssh"), str(home / "secrets"), str(shared)]
    ):
        assert _browse(client, str(home / ".ssh")).status_code == 400
        assert _browse(client, str(home / "secrets")).status_code == 400
        # 合法共享根(不在任何 home 子树内)不受影响
        assert _browse(client, str(shared)).status_code == 200
        # bob 自己的 home 根不受影响
        assert _browse(client, str(other)).status_code == 200


def test_shared_row_inside_any_home_subtree_filtered_read_side(fs_app, workspace):
    """读取侧纵深防御不依赖 creator 关联:任何用户 home 子树内的共享行都不放大。

    bob 自己 home 内的共享行对 alice 同样不可达(bob 本人仍可经自己的
    home 根访问该子树,不依赖共享根机制)。
    """
    ws, home, shared, other = workspace
    (other / "team").mkdir()
    client = fs_app.test_client()
    with _with_shared_paths(fs_app, workspace, [str(other / "team"), str(shared)]):
        assert _browse(client, str(other / "team")).status_code == 400
        assert _browse(client, str(shared)).status_code == 200


# --- review round 2 [3994613308]: check-path existence probing narrowed ----


def test_check_path_foreign_home_subtree_rejected(fs_app, workspace):
    """check-path 不得探测他人 home 子树(exists/canCreate 逐条探测=枚举)。"""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    # <base>/<别人的 account>/anything → 400
    resp = client.post("/api/fs/check-path", json={"path": str(other / "id_rsa")})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False
    # <base>/<别人的 account> 本身(一级但是 home)→ 400
    resp = client.post("/api/fs/check-path", json={"path": str(other)})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False


def test_check_path_second_level_under_base_rejected(fs_app, workspace):
    """<base>/x/y(二级)→ 400:一级非 home 之外不再放行。"""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    resp = client.post("/api/fs/check-path", json={"path": str(ws / "x" / "y")})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] is False


@pytest.mark.regression
def test_check_path_own_home_deep_and_workspace_point_still_valid(fs_app, workspace):
    """收窄后的正向保持:自己 home 子树任意深度 + base 自身 + 一级非 home。"""
    ws, home, shared, other = workspace
    client = fs_app.test_client()
    # 自己 home 根的完整子树(任意深度)
    resp = client.post("/api/fs/check-path", json={"path": str(home / "a" / "b" / "c")})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True
    # base dir 自身这一个点
    resp = client.post("/api/fs/check-path", json={"path": str(ws)})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True
    # 一级非 home 子路径(#2317)
    resp = client.post("/api/fs/check-path", json={"path": str(ws / "new-project")})
    assert resp.status_code == 200
    assert resp.get_json()["valid"] is True
