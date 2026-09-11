"""Unit tests for the user-url isolation gate (Issue #3374)."""

from unittest.mock import patch

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}


class _Config:
    enabled = True
    multi_user_mode = True


class _StubManager:
    def __init__(self):
        self.config = _Config()
        self.launched_with = None

    def get_user_webui_url(self, user_id, system_account, host_url):
        self.launched_with = system_account
        return "http://127.0.0.1:3100", "tok"

    def update_user_activity(self, user_id):
        pass

    def supports_per_user_launch(self, system_account):
        return True, None


def _db_user(system_account):
    return {"id": 7, "username": "alice", "system_account": system_account}


def _call(client, query=""):
    client.set_cookie("session_token", "test-token")
    with patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER):
        return client.get(f"/api/workspace/user-url{query}")


def _patch_stack(repo_user, stub):
    user_patch = patch("app.repositories.user_repo.UserRepository")
    manager_patch = patch("app.services.webui_manager.get_webui_manager", return_value=stub)
    repo_cls = user_patch.start()
    repo_cls.return_value.get_user_by_id.return_value = repo_user
    manager_patch.start()
    return user_patch, manager_patch


def _deployment_supported(monkeypatch):
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    monkeypatch.setattr(wic, "_is_docker_multi_user_mode", lambda: True)


def test_unauthenticated_request_is_401(app, client):
    resp = client.get("/api/workspace/user-url")
    assert resp.status_code == 401


def test_unknown_user_is_404(app, client):
    stub = _StubManager()
    patches = _patch_stack(None, stub)
    try:
        resp = _call(client)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 404


@pytest.mark.regression
@pytest.mark.parametrize("query", ["", "?required_isolation=none"])
def test_default_path_preserves_username_fallback(app, client, monkeypatch, query):
    # 回归保护:显式隔离要求缺席(或显式 none)时,username 回退(既有映射约定)
    # 必须保留——设计 §4.1 将缺省与 required_isolation=none 定义为等价。
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, query)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["system_account"] == "alice"  # username 回退保留
    assert stub.launched_with == "alice"
    assert body["isolation"]["local_workspace_multi_user"] == "supported"


def test_gate_rejects_missing_identity_mapping(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "identity_mapping_missing"
    assert body["isolation"]["policy_revision"] == wic.POLICY_REVISION
    assert stub.launched_with is None  # 未启动


def test_gate_rejects_unsupported_level(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=sandboxed")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "isolation_level_unsupported"


def test_gate_rejects_invalid_level(app, client):
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=strong")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "invalid_isolation_level"


def test_gate_rejects_shared_account_launch(app, client, monkeypatch):
    _deployment_supported(monkeypatch)

    class _SharedLaunchManager(_StubManager):
        def supports_per_user_launch(self, system_account):
            return False, "dev_directory_mode_shared_account"

    stub = _SharedLaunchManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "per_user_launch_unavailable"


def test_gate_passes_with_explicit_mapping(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["system_account"] == "alice_acct"
    assert stub.launched_with == "alice_acct"


@pytest.mark.regression
def test_https_multi_user_returns_relative_webui_path(app, client, monkeypatch):
    # 回归保护:HTTPS + 多用户时 /webui/<port>/ 相对路径分支不因插入点受破坏。
    # 凭证用 Authorization header 而非 cookie:werkzeug cookie jar 按域匹配,
    # localhost 域的 cookie 不会随 base_url="https://example.com/" 请求发送
    # (_extract_token() 依次读 cookie → Bearer header,header 与 host 无关)。
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    stub.get_user_webui_url = lambda user_id, sa, host: ("http://10.0.0.1:3100", "tok")
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        with patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER):
            resp = client.get(
                "/api/workspace/user-url",
                base_url="https://example.com/",
                headers={"Authorization": "Bearer test-token"},
            )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["url"] == "/webui/3100/"
