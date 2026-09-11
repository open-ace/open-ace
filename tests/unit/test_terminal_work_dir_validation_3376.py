"""Unit tests for terminal/vscode start path validation (Issue #3376)."""

from unittest.mock import patch

import pytest
from flask import g

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-111111111111"
USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}


class _StubAgentMgr:
    def __init__(self):
        self.sent = None

    def get_machine(self, machine_id):
        return {"machine_name": "m", "hostname": "h", "tenant_id": 1, "created_by": 1}

    def is_agent_connected(self, machine_id):
        return True

    def send_command(self, machine_id, cmd):
        self.sent = cmd

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


@pytest.mark.parametrize("bad", ["../../etc", "relative/dir", "/etc/x", "/opt/tool", 5, ["a", "b"]])
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


@pytest.mark.parametrize("bad", ["../../etc", "relative/dir", "/etc/x", 5])
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
