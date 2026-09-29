"""Unit tests for terminal/vscode start path validation (Issue #3376)."""

from unittest.mock import patch

import pytest
from flask import g

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-111111111111"
USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}

# NewSessionModal.getDefaultPath 的三个默认值(review round 1 items 6+7:
# 旧 is_valid_path 口径会把它们全部拒掉)。与前端字面量钉死同步。
FRONTEND_DEFAULT_WORK_DIRS = ["/root/workspace", "~/workspace", "C:\\workspace"]


class _StubAgentMgr:
    def __init__(self):
        self.sent = None
        self.commands = []

    def get_machine(self, machine_id):
        return {
            "machine_name": "m",
            "hostname": "h",
            "tenant_id": 1,
            "created_by": 1,
            "work_dir": "/root/workspace",
            "status": "online",
        }

    def is_agent_connected(self, machine_id):
        return True

    def send_command(self, machine_id, cmd):
        self.sent = cmd
        self.commands.append(cmd)

    def get_browse_result(self, request_id, timeout=None):
        return {"success": True, "result": {"path": "/root/workspace", "directories": []}}

    def get_backend_url(self, base):
        return "http://127.0.0.1:19888"

    def check_user_access(self, machine_id, user_id):
        return True

    def get_user_permission(self, machine_id, user_id):
        # machine_access_required 装饰器对非管理员调用(remote.py:957)
        return "user"


def _auth(monkeypatch):
    def _set_user():
        g.user = dict(USER)
        g.user_id = USER["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


@pytest.mark.parametrize(
    "bad",
    ["../../etc", "relative/dir", "/a/../b", "/a\x00b", 5, ["a", "b"], {"p": 1}],
)
def test_terminal_invalid_work_dir_rejected(app, client, monkeypatch, bad):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr),
        patch("app.modules.workspace.session_manager.get_session_manager") as sm_cls,
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": bad},
        )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert agent_mgr.sent is None


@pytest.mark.parametrize("bad", ["../../etc", "relative/dir", "/a/../b", 5, ["a", "b"]])
def test_vscode_invalid_project_path_rejected(app, client, monkeypatch, bad):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": bad},
        )
    assert resp.status_code == 400
    assert agent_mgr.sent is None


@pytest.mark.regression
@pytest.mark.parametrize("work_dir", FRONTEND_DEFAULT_WORK_DIRS)
def test_frontend_default_work_dirs_pass_terminal_start(app, client, monkeypatch, work_dir):
    """契约:三个前端默认 work_dir 必须能开终端(否则新会话弹窗全挂)。"""
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr),
        patch("app.modules.workspace.session_manager.get_session_manager") as sm_cls,
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": work_dir},
        )
    assert resp.status_code == 200
    assert agent_mgr.sent["work_dir"] == work_dir


@pytest.mark.regression
@pytest.mark.parametrize("project_path", FRONTEND_DEFAULT_WORK_DIRS)
def test_frontend_default_paths_pass_vscode_start(app, client, monkeypatch, project_path):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": project_path},
        )
    assert resp.status_code == 200
    assert agent_mgr.sent["project_path"] == project_path


@pytest.mark.regression
def test_remote_policy_locations_pass_through(app, client, monkeypatch):
    """/etc 等位置策略留给 agent:后端只做结构校验。"""
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/etc"},
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["project_path"] == "/etc"

        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/opt/tool"},
        )
        assert resp.status_code == 200


@pytest.mark.regression
def test_valid_and_empty_paths_pass_through(app, client, monkeypatch):
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr),
        patch("app.modules.workspace.session_manager.get_session_manager") as sm_cls,
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": "/home/alice/proj"},
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["work_dir"] == "/home/alice/proj"

        agent_mgr.sent = None
        resp = client.post("/api/remote/terminal/start", json={"machine_id": MACHINE_ID})
        assert resp.status_code == 200
        assert agent_mgr.sent["work_dir"] == ""

        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/home/alice/p"},
        )
        assert resp.status_code == 200
        assert agent_mgr.sent["command"] == "start_vscode"


# --- review round 1, item 10: falsy-hole semantics -------------------------


@pytest.mark.regression
@pytest.mark.parametrize("falsy", [0, False, [], {}])
def test_falsy_work_dir_treated_as_absent_not_bypass(app, client, monkeypatch, falsy):
    """0/False/[]/{} 归一为 ""(缺省 work_dir);非空非法值才 400。

    旧的 `work_dir and (...)` 守卫对 falsy 值整段跳过校验;现在语义显式:
    falsy = 未提供,truthy 非法 = 400。
    """
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr),
        patch("app.modules.workspace.session_manager.get_session_manager") as sm_cls,
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/start",
            json={"machine_id": MACHINE_ID, "work_dir": falsy},
        )
    assert resp.status_code == 200
    assert agent_mgr.sent["work_dir"] == ""


def test_cli_start_invalid_work_dir_rejected(app, client, monkeypatch):
    """item 10:/terminal/cli/start 此前完全无路径校验。"""
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr),
        patch("app.modules.workspace.session_manager.get_session_manager") as sm_cls,
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        sm_cls.return_value.create_session.return_value = None
        sm_cls.return_value.update_session_fields.return_value = True
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        proxy_cls.return_value.get_cli_settings_for_tool.return_value = {}
        resp = client.post(
            "/api/remote/terminal/cli/start",
            json={"machine_id": MACHINE_ID, "work_dir": "../../etc"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

        # 合法远端路径通过(含前端默认值)
        for wd in FRONTEND_DEFAULT_WORK_DIRS:
            resp = client.post(
                "/api/remote/terminal/cli/start",
                json={"machine_id": MACHINE_ID, "work_dir": wd},
            )
            assert resp.status_code == 200, wd


def test_machine_browse_path_validated(app, client, monkeypatch):
    """item 10:GET /machines/<id>/browse 的 path 参数补远端校验。"""
    _auth(monkeypatch)
    agent_mgr = _StubAgentMgr()
    with patch("app.routes.remote.get_remote_agent_manager", return_value=agent_mgr):
        resp = client.get(
            f"/api/remote/machines/{MACHINE_ID}/browse",
            query_string={"path": "../../etc"},
        )
        assert resp.status_code == 400

        # 无 path → 机器 work_dir 缺省;合法 path → 正常分发
        assert client.get(f"/api/remote/machines/{MACHINE_ID}/browse").status_code == 200
        assert (
            client.get(
                f"/api/remote/machines/{MACHINE_ID}/browse",
                query_string={"path": "/home/alice/proj"},
            ).status_code
            == 200
        )
