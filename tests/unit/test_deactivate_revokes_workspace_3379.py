"""Issue #3379 (PR-A): deactivation must stop the workspace and close tokens.

The #3374 acceptance checklist requires "停用用户/撤销 token 后无法恢复继续
执行". Before this fix, DELETE /admin/users/<id> revoked sessions but left the
user's WebUI instance running, its LLM proxy token live, and — because webui
tokens are stateless (signature + TTL) — every already-issued URL token kept
authenticating until its TTL lapsed (URL_TOKEN_ALLOWED_PATHS admits admin
routes).
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services import webui_manager as wm
from app.services.webui_manager import WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.issue(3379), pytest.mark.regression]


MOCK_ADMIN = {"id": 1, "username": "admin", "role": "admin", "tenant_id": 1}


class _ManagerStub:
    """Records stop calls; stands in for the manager singleton."""

    def __init__(self):
        self.stopped = []
        self.config = WorkspaceConfig(enabled=True, multi_user_mode=True)

    def stop_user_webui(self, user_id):
        self.stopped.append(user_id)


# ── token validation: the named user must be active and not deleted ─────


def _manager_with_secret(secret="s" * 64):
    return WebUIManager(WorkspaceConfig(enabled=True, token_secret=secret))


def test_v2_token_rejected_when_user_deactivated(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": False})
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deactivated" in err


def test_v2_token_rejected_when_user_soft_deleted(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(
        wm,
        "_webui_token_user",
        lambda uid: {"id": uid, "is_active": True, "deleted_at": "2026-09-13"},
    )
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deleted" in err


def test_v2_token_rejected_when_user_missing(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: None)
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "not found" in err


def test_v2_token_rejected_when_lookup_fails(monkeypatch):
    """Fail closed: a token that cannot be tied to a live user does not pass."""
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)

    def _boom(uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(wm, "_webui_token_user", _boom)
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "unavailable" in err


def test_v1_token_rejected_when_user_deactivated(monkeypatch):
    manager = _manager_with_secret()
    # Mint a v1 token directly (legacy shape, no TTL).
    import hashlib

    random_part = "deadbeef"
    signature = hashlib.sha256(f"7:3100:{random_part}:{'s' * 64}".encode()).hexdigest()[:16]
    token = f"7:3100:{random_part}:{signature}"
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": False})
    ok, user_id, err = manager.validate_token(token)
    assert ok is False and user_id is None
    assert "deactivated" in err


def test_active_user_token_still_validates(monkeypatch):
    manager = _manager_with_secret()
    token = manager.generate_token(7, 3100)
    monkeypatch.setattr(wm, "_webui_token_user", lambda uid: {"id": uid, "is_active": True})
    assert manager.validate_token(token) == (True, 7, None)


# ── deactivation routes: stop the workspace (fail-soft) ────────────────


def _delete_user_env(monkeypatch, stub):
    monkeypatch.setattr("app.routes.admin.get_webui_manager", lambda: stub, raising=False)
    # Patch the lazy import inside the handler.
    monkeypatch.setattr(wm, "get_webui_manager", lambda: stub)
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": 2,
        "is_active": True,
    }
    repo.delete_all_sessions_for_user.return_value = {
        "sessions": 1,
        "sso_sessions": 0,
        "web_user_auth_sessions": 0,
    }
    repo.delete_user.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    return repo


def test_delete_user_stops_webui_instance(app, client, monkeypatch):
    stub = _ManagerStub()
    repo = _delete_user_env(monkeypatch, stub)
    with patch("app.routes.admin.audit_logger") as audit:
        audit.log_action.return_value = True
        client.set_cookie("session_token", "t")
        with (
            patch(
                "app.auth.decorators._authenticate",
                return_value=(True, {**MOCK_ADMIN, "id": 1}),
            ),
            patch("app.routes.admin.same_tenant_user_required", lambda f: f),
        ):
            from flask import g

            # g.user_id is set by the auth decorator; emulate it.
            with client.application.test_request_context("/"):
                g.user_id = 1
                resp = client.delete("/api/admin/users/7")
    assert resp.status_code == 200
    assert stub.stopped == [7]
    assert repo.delete_all_sessions_for_user.called
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stopped") is True


def test_delete_user_proceeds_when_stop_fails(app, client, monkeypatch):
    """Fail-soft: a stop hiccup must not abort deactivation — the token-side
    active-user check still closes the door."""

    class _Exploding(_ManagerStub):
        def stop_user_webui(self, user_id):
            raise RuntimeError("manager locked")

    stub = _Exploding()
    repo = _delete_user_env(monkeypatch, stub)
    with patch("app.routes.admin.audit_logger") as audit:
        audit.log_action.return_value = True
        client.set_cookie("session_token", "t")
        with (
            patch(
                "app.auth.decorators._authenticate",
                return_value=(True, {**MOCK_ADMIN, "id": 1}),
            ),
            patch("app.routes.admin.same_tenant_user_required", lambda f: f),
        ):
            resp = client.delete("/api/admin/users/7")
    assert resp.status_code == 200
    assert repo.delete_user.called
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("workspace_stopped") is False


def test_deactivate_via_update_stops_webui_and_revokes_sessions(app, client, monkeypatch):
    """PUT /admin/users/<id> with is_active=false takes the same path."""
    stub = _ManagerStub()
    monkeypatch.setattr(wm, "get_webui_manager", lambda: stub)
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": 2,
        "role": "user",
        "is_active": True,
    }
    repo.update_user.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    with patch("app.routes.admin.audit_logger") as audit:
        audit.log_action.return_value = True
        client.set_cookie("session_token", "t")
        with (
            patch(
                "app.auth.decorators._authenticate",
                return_value=(True, {**MOCK_ADMIN, "id": 1}),
            ),
            patch("app.routes.admin.same_tenant_user_required", lambda f: f),
        ):
            resp = client.put("/api/admin/users/7", json={"is_active": False})
    assert resp.status_code == 200
    assert stub.stopped == [7]
    assert repo.delete_all_sessions_for_user.called
    details = audit.log_action.call_args.kwargs.get("details", {})
    assert details.get("status_change") == {"from": True, "to": False}
    assert details.get("workspace_stopped") is True


def test_reactivate_via_update_does_not_stop(app, client, monkeypatch):
    stub = _ManagerStub()
    monkeypatch.setattr(wm, "get_webui_manager", lambda: stub)
    repo = MagicMock()
    repo.get_user_by_id.return_value = {
        "id": 7,
        "username": "bob",
        "tenant_id": 2,
        "role": "user",
        "is_active": False,
    }
    repo.update_user.return_value = True
    monkeypatch.setattr("app.routes.admin.user_repo", repo)
    with patch("app.routes.admin.audit_logger") as audit:
        audit.log_action.return_value = True
        client.set_cookie("session_token", "t")
        with (
            patch(
                "app.auth.decorators._authenticate",
                return_value=(True, {**MOCK_ADMIN, "id": 1}),
            ),
            patch("app.routes.admin.same_tenant_user_required", lambda f: f),
        ):
            resp = client.put("/api/admin/users/7", json={"is_active": True})
    assert resp.status_code == 200
    assert stub.stopped == []
    assert not repo.delete_all_sessions_for_user.called
