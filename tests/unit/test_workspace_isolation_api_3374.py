"""Unit tests for the workspace isolation capability endpoint (Issue #3374)."""

from unittest.mock import patch

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_SESSION = (True, {"user_id": 42, "username": "alice", "role": "user", "tenant_id": 1})

# 真实 URL(app/__init__.py 的注册与 url_prefix);评审 #15:此前的独立蓝图
# fixture 自己复述了 prefix,删掉注册行的守卫能力是假的。
URL = "/api/workspace/isolation-capabilities"


class _StubConfig:
    enabled = True
    multi_user_mode = True


class _StubManager:
    config = _StubConfig()


@pytest.fixture
def stub_snapshot():
    return wic.build_workspace_isolation_snapshot(_StubManager())


def test_requires_authentication(app, client):
    resp = client.get(URL)
    assert resp.status_code == 401


def test_returns_contract_for_authenticated_user(app, client, stub_snapshot, monkeypatch):
    # 不构造真实 manager(只读端点不得铸造 secret/起清理 greenlet,评审 #13)
    monkeypatch.setattr("app.services.webui_manager.peek_webui_manager", lambda: None)
    with (
        patch(
            "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
            return_value=stub_snapshot,
        ),
        patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION),
    ):
        resp = client.get(URL, headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert resp.get_json() == stub_snapshot.public_dict()


def test_internal_error_is_structured(app, client, monkeypatch):
    monkeypatch.setattr("app.services.webui_manager.peek_webui_manager", lambda: None)
    with (
        patch(
            "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
            side_effect=RuntimeError("boom"),
        ),
        patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION),
    ):
        resp = client.get(URL, headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Internal server error"}
