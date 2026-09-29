"""Unit tests for sandboxed-mode project path validation (Issue #3427).

api_create_project in sandboxed isolation must validate the path against the
isolation-aware home roots fs browsing uses (fs._home_roots_for_user →
/workspace/<account>, account = system_account or username), reject
sibling-prefix paths, and skip host directory creation (the directory lives
inside the sandbox container).
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3427)]

USER = {
    "id": 11,
    "user_id": 11,
    "username": "qlfan",
    "system_account": "qlfan",
    "role": "user",
    "tenant_id": 1,
}


class _FakeInstance:
    def __init__(self, isolation_level):
        self.isolation_level = isolation_level


class _FakeManager:
    def __init__(self, isolation_level):
        self._instance = _FakeInstance(isolation_level)

    def get_user_instance(self, user_id):
        return self._instance


class _FakeProject:
    def to_dict(self):
        return {"id": 42, "path": "/workspace/qlfan/proj"}


def _make_app(isolation_level):
    from app.routes.projects import projects_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.before_request_funcs["projects"] = []

    @app.before_request
    def _set_user():
        g.user = dict(USER)
        g.user_id = USER["id"]
        g.user_role = USER["role"]
        g.tenant_id = USER["tenant_id"]
        return None

    return app


@pytest.fixture
def sandbox_client():
    app = _make_app("sandboxed")
    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch(
            "app.services.webui_manager.get_webui_manager",
            return_value=_FakeManager("sandboxed"),
        ),
        patch("app.routes.projects.project_repo.get_project_by_path", return_value=None),
        patch("app.routes.projects.project_repo.get_all_projects", return_value=[]),
        patch("app.routes.projects.project_repo.create_project", return_value=42),
        patch("app.routes.projects.project_repo.get_project_by_id", return_value=_FakeProject()),
        patch("app.routes.projects.run_as_user") as run_as_user,
    ):
        yield app.test_client(), run_as_user


def _post(client, path):
    return client.post(
        "/api/projects",
        json={"path": path, "name": "sandbox-proj", "create_dir": True},
    )


def test_sandboxed_accepts_path_under_workspace_home(sandbox_client):
    """Paths under /workspace/<account> are accepted and registered."""
    client, run_as_user = sandbox_client
    resp = _post(client, "/workspace/qlfan/myproject")
    assert resp.status_code == 201
    assert resp.get_json()["success"] is True
    # sandboxed 模式不碰宿主机文件系统
    run_as_user.assert_not_called()


def test_sandboxed_accepts_home_root_itself(sandbox_client):
    """The home root /workspace/<account> itself is a valid project path."""
    client, _ = sandbox_client
    assert _post(client, "/workspace/qlfan").status_code == 201


def test_sandboxed_rejects_sibling_prefix(sandbox_client):
    """/workspace/<account>evil must NOT pass a naive startswith check."""
    client, _ = sandbox_client
    resp = _post(client, "/workspace/qlfanevil/proj")
    assert resp.status_code == 400
    assert "not allowed" in resp.get_json()["error"]


def test_sandboxed_rejects_other_users_home(sandbox_client):
    client, _ = sandbox_client
    assert _post(client, "/workspace/bob/proj").status_code == 400


def test_sandboxed_rejects_host_base_dir_path(sandbox_client):
    """Host workspace base dirs are not valid in sandboxed mode."""
    client, _ = sandbox_client
    resp = _post(client, "/home/qlfan/proj")
    assert resp.status_code == 400
    assert "/workspace/qlfan" in resp.get_json()["error"]


def test_sandboxed_rejects_shared_projects(sandbox_client):
    """#3376 shared-project topology is host-defined; sandbox mode rejects
    shared registration explicitly instead of a host-flavoured error."""
    client, _ = sandbox_client
    resp = client.post(
        "/api/projects",
        json={
            "path": "/workspace/qlfan/team",
            "name": "team",
            "is_shared": True,
            "create_dir": True,
        },
    )
    assert resp.status_code == 400
    assert "not supported in sandboxed" in resp.get_json()["error"]


def test_sandboxed_rejects_identity_less_user():
    """No system_account/username → no home roots → clean 400."""
    app = _make_app("sandboxed")

    @app.before_request
    def _strip_identity():
        g.user = {"id": 12, "user_id": 12, "role": "user", "tenant_id": 1}
        g.user_id = 12
        return None

    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch(
            "app.services.webui_manager.get_webui_manager",
            return_value=_FakeManager("sandboxed"),
        ),
    ):
        client = app.test_client()
        resp = _post(client, "/workspace/qlfan/proj")
    assert resp.status_code == 400
    assert "No sandbox home directory" in resp.get_json()["error"]


def test_non_sandboxed_uses_original_validation():
    """Without a sandboxed instance the original host-path validation runs."""
    app = _make_app(None)
    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch(
            "app.services.webui_manager.get_webui_manager",
            return_value=_FakeManager("os_user"),
        ),
        patch("app.routes.projects.project_repo.get_project_by_path", return_value=None),
        patch("app.routes.projects.project_repo.get_all_projects", return_value=[]),
        patch("app.routes.projects.project_repo.create_project", return_value=42),
        patch("app.routes.projects.project_repo.get_project_by_id", return_value=_FakeProject()),
        patch("app.routes.projects.get_effective_system_account", return_value=None),
        patch("app.routes.projects.os.makedirs") as makedirs,
    ):
        client = app.test_client()
        resp = _post(client, "/workspace/qlfan/proj")

    # 原宿主机逻辑执行:目录走宿主机 makedirs(create_dir 未被沙箱分支跳过)
    assert resp.status_code == 201
    assert makedirs.called
