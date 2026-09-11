"""Unit tests for the user-url isolation gate (Issue #3374)."""

from unittest.mock import patch

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}


class _Config:
    enabled = True
    multi_user_mode = True
    required_isolation_level = ""


class _StubManager:
    def __init__(self, multi_user_mode=True):
        self.config = _Config()
        self.config.multi_user_mode = multi_user_mode
        self.launched_with = None

    def get_user_webui_url(self, user_id, system_account, host_url):
        self.launched_with = system_account
        return "http://127.0.0.1:3100", "tok"

    def update_user_activity(self, user_id):
        pass

    def supports_per_user_launch(self, system_account):
        return True, None

    def per_user_launch_readiness(self):
        return None  # healthy launch path


class _DegradedManager(_StubManager):
    def per_user_launch_readiness(self):
        return "launch_wrapper_missing"


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
def test_single_user_mode_preserves_username_fallback(app, client, monkeypatch, query):
    # 回归保护:单用户模式(隔离下限为 none)下,username 回退(既有映射约定)
    # 必须保留——多用户模式的默认 fail-closed 见下方用例(评审 #12)。
    stub = _StubManager(multi_user_mode=False)
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
    assert body["isolation"]["local_workspace_multi_user"] == "unsupported"


@pytest.mark.regression
def test_multi_user_default_path_fails_closed_without_mapping(app, client, monkeypatch):
    # 评审 #1/#12:多用户模式下隔离下限默认为 os_user,登录不再回填
    # system_account,缺映射的默认路径(无参数)结构化拒绝而非静默启动
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "identity_mapping_missing"
    assert stub.launched_with is None


def test_param_cannot_lower_config_floor(app, client, monkeypatch):
    # 评审 #12:请求参数只能抬高下限——多用户模式下 ?required_isolation=none
    # 不会把默认的 os_user 下限降级
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "?required_isolation=none")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "identity_mapping_missing"


def test_multi_user_default_path_with_explicit_mapping(app, client, monkeypatch):
    # 评审 #12:多用户模式默认路径对有显式映射的用户正常放行(默认 UI 不退化)
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["system_account"] == "alice_acct"
    assert stub.launched_with == "alice_acct"


def test_empty_param_counts_as_absent(app, client, monkeypatch):
    # 评审 #14:?required_isolation=(空串/空白)视为缺省,不产生 invalid 400
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=")
        resp_ws = _call(client, "?required_isolation=%20")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp_ws.status_code == 200


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


def test_rejection_reasons_keep_both_namespaces(app, client, monkeypatch):
    # 评审 #7:部署级 reasons 与请求级拒绝并存——参数笔误不应顶掉部署原因
    _deployment_supported(monkeypatch)
    # 平台改回真实值使快照带部署级 reason
    monkeypatch.setattr(wic, "_current_platform", lambda: "darwin")
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=strong")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    body = resp.get_json()
    codes = [r["code"] for r in body["reasons"]]
    assert "platform_unsupported" in codes
    assert "invalid_isolation_level" in codes


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


def test_invalid_config_floor_falls_back_to_derived_default(app, client, monkeypatch):
    # 手写的无效 config 下限不得让路由 500(KeyError),回退派生默认
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    stub.config.required_isolation_level = "bogus"
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "")
        resp_param = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp_param.status_code == 200


# --- package-method (non-Docker) form: PR review round 2 ---


def test_package_mode_default_path_with_mapping_is_200(app, client, monkeypatch):
    # 硬回归钉:包安装多用户形态(非 Docker 布局)默认路径不再全线 400——
    # 下限从快照(探针验证的等级)派生,而非 Docker 布局
    _deployment_supported(monkeypatch)
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    stub = _StubManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["system_account"] == "alice_acct"


def test_package_mode_mappingless_default_is_identity_400(app, client, monkeypatch):
    # 探针健康的包安装形态:快照 os_user → 下限 os_user → 缺映射仍拒绝
    _deployment_supported(monkeypatch)
    stub = _StubManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "identity_mapping_missing"


def test_package_mode_degraded_default_path_restores_fallback(app, client, monkeypatch):
    # 探针降级(如缺 wrapper)的包安装形态:快照 none → 下限 none → 默认路径
    # 保持旧行为(username 回退),修复"全线 400"回归
    _deployment_supported(monkeypatch)
    stub = _DegradedManager()
    patches = _patch_stack(_db_user(None), stub)
    try:
        resp = _call(client, "")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    assert resp.get_json()["system_account"] == "alice"


def test_package_mode_degraded_explicit_os_user_is_rejected(app, client, monkeypatch):
    _deployment_supported(monkeypatch)
    stub = _DegradedManager()
    patches = _patch_stack(_db_user("alice_acct"), stub)
    try:
        resp = _call(client, "?required_isolation=os_user")
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 400
    assert resp.get_json()["error_code"] == "isolation_level_unsupported"
