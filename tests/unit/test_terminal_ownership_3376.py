"""Unit tests for terminal session ownership (Issue #3376)."""

from unittest.mock import patch

import pytest
from flask import g

from app.modules.workspace.terminal_store import terminal_info_store

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-222222222222"
TERM_ID = "0b6a2b6e-8f0b-4f5c-9c1e-333333333333"

OWNER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}
OTHER = {"id": 9, "user_id": 9, "username": "mallory", "role": "user", "tenant_id": 1}
TENANT_ADMIN = {"id": 5, "user_id": 5, "username": "ta", "role": "tenant_admin", "tenant_id": 1}
CROSS_TA = {"id": 6, "user_id": 6, "username": "xta", "role": "tenant_admin", "tenant_id": 2}
PLATFORM_ADMIN = {
    "id": 1,
    "user_id": 1,
    "username": "root",
    "role": "platform_admin",
    "tenant_id": 1,
}


class _StubAgentMgr:
    def get_machine(self, machine_id):
        return {"tenant_id": 1, "created_by": 1}

    def check_user_access(self, machine_id, user_id):
        return True

    def get_user_permission(self, machine_id, user_id):
        # machine_access_required 装饰器对非管理员调用(remote.py:957)
        return "user"

    def send_command(self, machine_id, cmd):
        pass

    def get_backend_url(self, base):
        return "http://127.0.0.1:19888"


class _StubSession:
    def __init__(self, user_id, tenant_id=1):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.context = {}


def _auth(monkeypatch, user):
    def _set_user():
        g.user = dict(user)
        g.user_id = user["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


def _with_session(user_id, tenant_id=1):
    sm = type("SM", (), {})()
    sm.get_session = lambda sid, **kw: (
        _StubSession(user_id, tenant_id) if user_id is not None else None
    )
    return patch("app.modules.workspace.session_manager.get_session_manager", return_value=sm)


@pytest.fixture
def stored_terminal():
    terminal_info_store.put(
        MACHINE_ID,
        TERM_ID,
        {
            "status": "running",
            "ws_url": f"/api/remote/terminal/{TERM_ID}/ws",
            "token": "browser-token",
            "original_ws_url": "ws://10.0.0.1:42000/ws",
            "original_token": "agent-side-token",
        },
    )
    yield
    terminal_info_store.pop(MACHINE_ID, TERM_ID)


def _mgr(recorder):
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: recorder.append(cmd)
    return mgr


def _status(client):
    return client.get(f"/api/remote/terminal/{TERM_ID}/status?machine_id={MACHINE_ID}")


def _attach(client):
    return client.post(f"/api/remote/terminal/{TERM_ID}/attach", json={"machine_id": MACHINE_ID})


def _stop(client):
    # 实际路由:POST /api/remote/terminal/stop,body 含 terminal_id(remote.py:3646-3658)
    return client.post(
        "/api/remote/terminal/stop",
        json={"terminal_id": TERM_ID, "machine_id": MACHINE_ID},
    )


def test_status_owner_gets_info_without_agent_credentials(
    app, client, monkeypatch, stored_terminal
):
    _auth(monkeypatch, OWNER)
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
    ):
        resp = _status(client)
    assert resp.status_code == 200
    term = resp.get_json()["terminal"]
    assert term["token"] == "browser-token"
    assert "original_token" not in term and "original_ws_url" not in term


def test_status_non_owner_machine_user_is_403(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
    ):
        resp = _status(client)
    assert resp.status_code == 403


def test_status_cross_tenant_tenant_admin_is_403(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, CROSS_TA)
    with (
        _with_session(7, tenant_id=1),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
    ):
        resp = _status(client)
    assert resp.status_code == 403


def test_status_same_tenant_tenant_admin_and_platform_admin_allowed(
    app, client, monkeypatch, stored_terminal
):
    for user in (TENANT_ADMIN, PLATFORM_ADMIN):
        _auth(monkeypatch, user)
        with (
            _with_session(7),
            patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
        ):
            resp = _status(client)
        assert resp.status_code == 200, user["username"]


def test_status_missing_session_fail_closed(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    with (
        _with_session(None),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
    ):
        resp = _status(client)
    assert resp.status_code == 404


@pytest.mark.regression
def test_status_proxy_token_path_returns_full_info(app, client, monkeypatch, stored_terminal):
    # 内部 WS 代理(多 Pod 重定向)凭 browser token 本身认证,响应保留 original_*
    _auth(monkeypatch, OTHER)
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()),
    ):
        client.set_cookie("session_token", "browser-token")
        resp = _status(client)
    assert resp.status_code == 200
    term = resp.get_json()["terminal"]
    assert term["original_token"] == "agent-side-token"


def test_attach_non_owner_403_and_no_command(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    sent = []
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)),
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        resp = _attach(client)
    assert resp.status_code == 403
    assert sent == []


def test_attach_owner_succeeds(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OWNER)
    sent = []
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)),
        patch("app.routes.remote.get_api_key_proxy_service") as proxy_cls,
    ):
        proxy_cls.return_value.generate_proxy_token.return_value = "pt"
        resp = _attach(client)
    assert resp.status_code == 200
    assert sent and sent[0]["command"] == "attach_terminal"


def test_stop_non_owner_403_and_no_command(app, client, monkeypatch, stored_terminal):
    _auth(monkeypatch, OTHER)
    sent = []
    with (
        _with_session(7),
        patch("app.routes.remote.get_remote_agent_manager", return_value=_mgr(sent)),
    ):
        resp = _stop(client)
    assert resp.status_code == 403
    assert sent == []
