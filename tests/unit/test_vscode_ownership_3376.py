"""Unit tests for VSCode session ownership (Issue #3376)."""

import time
from unittest.mock import patch

import pytest
from flask import g

from app.modules.workspace.vscode_store import vscode_info_store, vscode_owner_store

pytestmark = [pytest.mark.issue(3376)]

MACHINE_ID = "0b6a2b6e-8f0b-4f5c-9c1e-444444444444"
VS_ID = "0b6a2b6e-8f0b-4f5c-9c1e-555555555555"

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
    def __init__(self, created_by=1):
        self._created_by = created_by

    def get_machine(self, machine_id):
        return {"tenant_id": 1, "created_by": self._created_by}

    def is_agent_connected(self, machine_id):
        return True

    def send_command(self, machine_id, cmd):
        pass

    def check_user_access(self, machine_id, user_id):
        return True

    def get_user_permission(self, machine_id, user_id):
        return "user"


def _running_info(owner_user_id=7, tenant_id=1):
    return {
        "status": "running",
        "original_http_url": "http://10.0.0.1:8080",
        "original_token": "ot",
        "cs_password": "pw",
        "token": "browser-token",
        "machine_id": MACHINE_ID,
        "project_path": "/p",
        "owner_user_id": owner_user_id,
        "tenant_id": tenant_id,
        "created_at": time.time(),
        "expires_at": time.time() + 3600,
    }


def _auth(monkeypatch, user):
    def _set_user():
        g.user = dict(user)
        g.user_id = user["id"]
        return True

    monkeypatch.setattr("app.routes.remote._set_user_from_token", _set_user)


@pytest.fixture
def running_session():
    vscode_info_store.put(MACHINE_ID, VS_ID, _running_info())
    yield
    vscode_info_store.mark_stopped(MACHINE_ID, VS_ID)


# --- owner store ---


def test_owner_store_record_pop_roundtrip():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    assert vscode_owner_store.pop(VS_ID) == (MACHINE_ID, 7, 1)
    assert vscode_owner_store.pop(VS_ID) is None  # consumed


def test_owner_store_peek_is_non_consuming():
    """Review round 1 [2]: peek 沿用 TTL,但不消费条目。"""
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    assert vscode_owner_store.peek(VS_ID) == (MACHINE_ID, 7, 1)
    assert vscode_owner_store.peek(VS_ID) == (MACHINE_ID, 7, 1)  # 仍在
    assert vscode_owner_store.pop(VS_ID) == (MACHINE_ID, 7, 1)  # pop 仍可消费
    assert vscode_owner_store.peek(VS_ID) is None


def test_owner_store_peek_expired_entry_returns_none():
    import time as _time

    from app.modules.workspace.vscode_store import VSCodeOwnerStore

    store = VSCodeOwnerStore(ttl=60.0)
    # 直接写入已过期的 recorded_at,避免 ttl=0.0 的时钟粒度抖动
    store._owners[VS_ID] = (MACHINE_ID, 7, 1, _time.time() - 61.0)
    assert store.peek(VS_ID) is None


def test_owner_store_lookup_is_keyed_by_vscode_id():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    assert vscode_owner_store.pop("00000000-0000-0000-0000-999999999999") is None
    # 无关 vscode_id 的查询不影响 VS_ID 的记录
    assert vscode_owner_store.pop(VS_ID) == (MACHINE_ID, 7, 1)


def test_owner_store_expired_entry_returns_none(monkeypatch):
    from app.modules.workspace.vscode_store import VSCodeOwnerStore

    store = VSCodeOwnerStore(ttl=0.0)
    store.record(VS_ID, MACHINE_ID, 7, 1)
    assert store.pop(VS_ID) is None  # recorded_at 已超 ttl
    assert store._owners == {}  # 过期条目被 pop 消费,不驻留


# --- reported owner resolution ---


def test_resolve_owner_prefers_recorded_requester():
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    from app.routes.remote import _resolve_vscode_reported_owner

    assert _resolve_vscode_reported_owner(_StubAgentMgr(created_by=1), MACHINE_ID, VS_ID) == 7


def test_resolve_owner_repeated_reports_resolve_same_owner():
    """Review round 1 [2]: 第二次 running 上报不得落到 machine.created_by。"""
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    from app.routes.remote import _resolve_vscode_reported_owner

    mgr = _StubAgentMgr(created_by=1)
    first = _resolve_vscode_reported_owner(mgr, MACHINE_ID, VS_ID)
    second = _resolve_vscode_reported_owner(mgr, MACHINE_ID, VS_ID)
    assert first == second == 7
    vscode_owner_store.pop(VS_ID)


def test_resolve_owner_falls_back_to_machine_creator():
    from app.routes.remote import _resolve_vscode_reported_owner

    assert _resolve_vscode_reported_owner(_StubAgentMgr(created_by=3), MACHINE_ID, VS_ID) == 3


def test_resolve_owner_machine_mismatch_falls_back_to_creator():
    # 记录属于另一台机器:防御性不采用,回退 created_by
    other_machine = "11111111-1111-1111-1111-111111111111"
    vscode_owner_store.record(VS_ID, other_machine, 7, 1)
    from app.routes.remote import _resolve_vscode_reported_owner

    assert _resolve_vscode_reported_owner(_StubAgentMgr(created_by=3), MACHINE_ID, VS_ID) == 3
    vscode_owner_store.pop(VS_ID)


# --- report wiring via agent_message ---


def test_agent_message_running_report_uses_requester_as_owner(app, client, monkeypatch):
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    mgr = _StubAgentMgr()
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=mgr),
        patch("app.routes.remote._check_legacy_fallback", return_value=(True, None)),
    ):
        resp = client.post(
            "/api/remote/agent/message",
            json={
                "type": "vscode_status",
                "machine_id": MACHINE_ID,
                "vscode_id": VS_ID,
                "status": "running",
                "http_url": "http://10.0.0.1:8080",
                "token": "ot",
                "cs_password": "pw",
                "project_path": "/p",
            },
        )
    assert resp.status_code == 200
    found = vscode_info_store.find_by_vscode_id(VS_ID)
    assert found is not None
    assert found[1]["owner_user_id"] == 7
    vscode_info_store.mark_stopped(MACHINE_ID, VS_ID)


def test_agent_message_repeated_running_reports_keep_owner(app, client, monkeypatch):
    """Review round 1 [2]: re-attach 重发 running,owner 不得漂移到 created_by。"""
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    mgr = _StubAgentMgr(created_by=1)
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=mgr),
        patch("app.routes.remote._check_legacy_fallback", return_value=(True, None)),
    ):
        for _ in range(2):
            resp = client.post(
                "/api/remote/agent/message",
                json={
                    "type": "vscode_status",
                    "machine_id": MACHINE_ID,
                    "vscode_id": VS_ID,
                    "status": "running",
                    "http_url": "http://10.0.0.1:8080",
                    "token": "ot",
                    "cs_password": "pw",
                    "project_path": "/p",
                },
            )
            assert resp.status_code == 200
            found = vscode_info_store.find_by_vscode_id(VS_ID)
            assert found is not None
            # 第二次上报曾因 pop 消费落到 machine.created_by(=1)
            assert found[1]["owner_user_id"] == 7
    vscode_info_store.mark_stopped(MACHINE_ID, VS_ID)


def test_agent_message_error_report_attributes_tenant_and_owner(app, client, monkeypatch):
    """Review round 1 [9]: error 态会话不再无主。"""
    vscode_owner_store.record(VS_ID, MACHINE_ID, 7, 1)
    mgr = _StubAgentMgr(created_by=1)
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=mgr),
        patch("app.routes.remote._check_legacy_fallback", return_value=(True, None)),
    ):
        resp = client.post(
            "/api/remote/agent/message",
            json={
                "type": "vscode_status",
                "machine_id": MACHINE_ID,
                "vscode_id": VS_ID,
                "status": "error",
                "error": "boom",
            },
        )
    assert resp.status_code == 200
    found = vscode_info_store.find_by_vscode_id(VS_ID)
    assert found is not None
    assert found[1]["status"] == "error"
    assert found[1]["tenant_id"] == 1
    assert found[1]["owner_user_id"] == 7
    vscode_info_store.pop(MACHINE_ID, VS_ID)


@pytest.fixture
def error_session():
    """租户 1 的 error 态会话(带归属)。"""
    vscode_info_store.put(
        MACHINE_ID,
        VS_ID,
        {
            "status": "error",
            "error": "boom",
            "machine_id": MACHINE_ID,
            "tenant_id": 1,
            "owner_user_id": 7,
        },
    )
    yield
    vscode_info_store.pop(MACHINE_ID, VS_ID)


def test_cross_tenant_admin_denied_on_error_session_status(app, client, monkeypatch, error_session):
    """Review round 1 [9]: 租户 2 的 tenant_admin 对租户 1 无主(error)会话被拒。"""
    _auth(monkeypatch, CROSS_TA)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 403


def test_cross_tenant_admin_denied_on_error_session_stop(app, client, monkeypatch, error_session):
    _auth(monkeypatch, CROSS_TA)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(
            "/api/remote/vscode/stop",
            json={"machine_id": MACHINE_ID, "vscode_id": VS_ID},
        )
    assert resp.status_code == 403
    assert sent == []


def test_unattributed_session_not_a_tenant_admin_wildcard(app, client, monkeypatch):
    """Review round 1 [9]: session_tenant None + 严格相等 → 归属不明即拒绝。"""
    vscode_info_store.put(
        MACHINE_ID, VS_ID, {"status": "error", "error": "x", "machine_id": MACHINE_ID}
    )
    try:
        _auth(monkeypatch, CROSS_TA)  # tenant 2 的 tenant_admin
        with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
            resp = _status(client)
        assert resp.status_code == 403
    finally:
        vscode_info_store.pop(MACHINE_ID, VS_ID)


# --- store-miss fail-closed (review round 1, item 3) ------------------------


def test_stop_without_store_record_fail_closed_404(app, client, monkeypatch):
    """k8s 多副本/重启丢失 store 时,stop 不得跳过所有权门闸。"""
    _auth(monkeypatch, OWNER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    # 确保无记录
    vscode_info_store.pop(MACHINE_ID, "00000000-0000-0000-0000-aaaaaaaaaaaa")
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=mgr),
        patch(
            "app.modules.workspace.vscode_store.vscode_info_store.find_by_vscode_id",
            return_value=None,
        ),
    ):
        resp = client.post(
            "/api/remote/vscode/stop",
            json={"machine_id": MACHINE_ID, "vscode_id": VS_ID},
        )
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "vscode_session_not_found"
    assert sent == []


def test_attach_without_store_record_fail_closed_404(app, client, monkeypatch):
    _auth(monkeypatch, OWNER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with (
        patch("app.routes.remote.get_remote_agent_manager", return_value=mgr),
        patch(
            "app.modules.workspace.vscode_store.vscode_info_store.find_by_vscode_id",
            return_value=None,
        ),
    ):
        resp = client.post(f"/api/remote/vscode/{VS_ID}/attach", json={"machine_id": MACHINE_ID})
    assert resp.status_code == 404
    assert resp.get_json()["error_code"] == "vscode_session_not_found"
    assert sent == []


# --- endpoint gates ---


def _status(client):
    return client.get(f"/api/remote/vscode/{VS_ID}/status")


def test_status_owner_gets_url_with_token(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OWNER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 200
    assert "token=" in resp.get_json().get("url", "")


def test_status_non_owner_403_without_url(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 403
    assert "url" not in resp.get_json()


def test_status_tenant_admin_and_platform_admin_allowed(app, client, monkeypatch, running_session):
    for user in (TENANT_ADMIN, PLATFORM_ADMIN):
        _auth(monkeypatch, user)
        with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
            resp = _status(client)
        assert resp.status_code == 200, user["username"]


def test_status_cross_tenant_tenant_admin_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, CROSS_TA)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = _status(client)
    assert resp.status_code == 403


def test_stop_non_owner_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(
            "/api/remote/vscode/stop",
            json={"machine_id": MACHINE_ID, "vscode_id": VS_ID},
        )
    assert resp.status_code == 403
    assert sent == []


def test_attach_non_owner_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, OTHER)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(f"/api/remote/vscode/{VS_ID}/attach", json={"machine_id": MACHINE_ID})
    assert resp.status_code == 403
    assert sent == []


def test_attach_cross_tenant_tenant_admin_403(app, client, monkeypatch, running_session):
    _auth(monkeypatch, CROSS_TA)
    sent = []
    mgr = _StubAgentMgr()
    mgr.send_command = lambda mid, cmd: sent.append(cmd)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=mgr):
        resp = client.post(f"/api/remote/vscode/{VS_ID}/attach", json={"machine_id": MACHINE_ID})
    assert resp.status_code == 403
    assert sent == []


@pytest.mark.regression
def test_start_records_requester_as_owner(app, client, monkeypatch):
    _auth(monkeypatch, OWNER)
    with patch("app.routes.remote.get_remote_agent_manager", return_value=_StubAgentMgr()):
        resp = client.post(
            "/api/remote/vscode/start",
            json={"machine_id": MACHINE_ID, "project_path": "/home/alice/p"},
        )
    assert resp.status_code == 200
    vscode_id = resp.get_json()["vscode_id"]
    assert vscode_owner_store.pop(vscode_id) == (MACHINE_ID, 7, 1)
