"""Route-level TDD tests for SSO email-linking gate and audit logging (PR #1788).

These tests exercise app/routes/sso.py helpers (_finalize_sso_login, logout)
directly with stubbed dependencies, avoiding the flaky sqlite-backed SSO manager
fixture. They target the security logic of the route, not the DB layer.

Findings covered:
6. Auto-provisioning must not link to an existing local account by email unless an
   admin opts in via allow_email_linking (medium, privilege escalation).
7. SAML login + logout must be audit-logged (medium, forensic gap).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.governance.audit_logger import AuditAction
from app.modules.sso.provider import SSOAuthResult, SSOProviderConfig, SSOToken, SSOUser
from app.modules.sso.saml import SAMLProvider
from app.routes import sso as sso_module

SP_ENTITY_ID = "https://openace.example.com/saml/metadata"
IDP_ENTITY_ID = "https://idp.example.com/metadata"
IDP_SSO_URL = "https://example.com/sso"


def _make_saml_provider(extra_params: dict | None = None) -> SAMLProvider:
    params = {"idp_entity_id": IDP_ENTITY_ID, "idp_x509_cert": "stubcert"}
    if extra_params:
        params.update(extra_params)
    return SAMLProvider(
        SSOProviderConfig(
            name="corp-saml",
            provider_type="saml",
            client_id=SP_ENTITY_ID,
            client_secret="",
            authorization_url=IDP_SSO_URL,
            token_url="",
            redirect_uri="",
            issuer_url=IDP_ENTITY_ID,
            extra_params=params,
        )
    )


def _auth_result(email="alice@example.com", provider_user_id="idp-user-1") -> SSOAuthResult:
    return SSOAuthResult(
        success=True,
        user=SSOUser(
            provider="corp-saml",
            provider_user_id=provider_user_id,
            email=email,
            username=provider_user_id,
            email_verified=False,
            raw_data={},
        ),
        token=SSOToken(access_token="saml:tok", token_type="SAML", expires_in=3600),
    )


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config["TESTING"] = True
    with app.test_request_context("/"):
        yield app


# ---------------------------------------------------------------------------
# Finding 6: email-linking gated behind allow_email_linking.
# ---------------------------------------------------------------------------


def test_finalize_sso_login_does_not_link_by_email_by_default(app_ctx):
    """Without allow_email_linking, _finalize_sso_login must not look up an existing
    local user by email and bind the SSO identity onto it."""
    manager = MagicMock()
    manager.get_user_by_sso_identity.return_value = None  # no existing SSO identity
    manager.link_identity.return_value = None
    manager.create_sso_session.return_value = "session-token"

    # An attacker-controlled email that collides with an existing local admin.
    user_repo = MagicMock()
    existing_admin = MagicMock()
    existing_admin.get.return_value = 1  # would-be victim account id
    user_repo.get_user_by_email.return_value = existing_admin
    user_repo.create_session.return_value = None

    audit_logger = MagicMock()

    provider = _make_saml_provider()  # allow_email_linking NOT set
    manager.get_provider.return_value = provider

    with (
        patch.object(sso_module, "get_sso_manager", return_value=manager),
        patch.object(sso_module, "user_repo", user_repo),
        patch.object(sso_module, "get_audit_logger", return_value=audit_logger),
        patch.object(sso_module, "_create_user_from_sso", return_value=99),
        patch.object(sso_module, "_get_session_timeout_hours", return_value=1),
    ):
        sso_module._finalize_sso_login("corp-saml", _auth_result(email="admin@example.com"), None)

    # The legacy email-based lookup must NOT happen when the gate is closed.
    user_repo.get_user_by_email.assert_not_called()
    # Identity must be linked onto the freshly provisioned user, never the existing one.
    manager.link_identity.assert_called_once()
    assert manager.link_identity.call_args.kwargs["user_id"] == 99


def test_finalize_sso_login_links_by_email_when_admin_opts_in(app_ctx):
    """When allow_email_linking is enabled, the legacy email-lookup path is restored."""
    manager = MagicMock()
    manager.get_user_by_sso_identity.return_value = None
    manager.create_sso_session.return_value = "session-token"

    user_repo = MagicMock()
    existing_user = {"id": 7}
    user_repo.get_user_by_email.return_value = existing_user
    user_repo.create_session.return_value = None

    audit_logger = MagicMock()

    provider = _make_saml_provider(extra_params={"allow_email_linking": True})
    manager.get_provider.return_value = provider

    with (
        patch.object(sso_module, "get_sso_manager", return_value=manager),
        patch.object(sso_module, "user_repo", user_repo),
        patch.object(sso_module, "UserRepository", return_value=user_repo),
        patch.object(sso_module, "get_audit_logger", return_value=audit_logger),
        patch.object(sso_module, "_create_user_from_sso") as create_mock,
        patch.object(sso_module, "_get_session_timeout_hours", return_value=1),
    ):
        sso_module._finalize_sso_login("corp-saml", _auth_result(email="match@example.com"), None)

    user_repo.get_user_by_email.assert_called_once_with("match@example.com")
    manager.link_identity.assert_called_once()
    assert manager.link_identity.call_args.kwargs["user_id"] == 7
    create_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Finding 7: SAML login + logout audit-logged.
# ---------------------------------------------------------------------------


def test_finalize_sso_login_emits_audit_login(app_ctx):
    """A successful SSO login must emit an audit LOGIN record."""
    manager = MagicMock()
    manager.get_user_by_sso_identity.return_value = 42
    manager.create_sso_session.return_value = "session-token"

    user_repo = MagicMock()
    user_repo.create_session.return_value = None

    audit_logger = MagicMock()
    provider = _make_saml_provider()
    manager.get_provider.return_value = provider

    with (
        patch.object(sso_module, "get_sso_manager", return_value=manager),
        patch.object(sso_module, "user_repo", user_repo),
        patch.object(sso_module, "UserRepository", return_value=user_repo),
        patch.object(sso_module, "get_audit_logger", return_value=audit_logger),
        patch.object(sso_module, "_get_session_timeout_hours", return_value=1),
    ):
        sso_module._finalize_sso_login("corp-saml", _auth_result(), None)

    actions = [
        call.kwargs.get("action") or call.args[0] for call in audit_logger.log.call_args_list
    ]
    assert AuditAction.LOGIN.value in actions


def test_logout_emits_audit_logout(app_ctx):
    """An SSO logout must emit an audit LOGOUT record."""
    from flask import Blueprint

    app = Flask(__name__)
    app.config["TESTING"] = True
    # Register the sso blueprint to exercise the real route handler.
    app.register_blueprint(sso_module.sso_bp)

    manager = MagicMock()
    manager.get_sso_session.return_value = {"user_id": 42, "provider_name": "corp-saml"}
    manager.delete_sso_session.return_value = True

    audit_logger = MagicMock()

    with (
        patch.object(sso_module, "get_sso_manager", return_value=manager),
        patch.object(sso_module, "get_audit_logger", return_value=audit_logger),
    ):
        client = app.test_client()
        resp = client.delete("/api/sso/session", headers={"Authorization": "Bearer saml:tok"})

    assert resp.status_code == 200
    actions = [
        call.kwargs.get("action") or call.args[0] for call in audit_logger.log.call_args_list
    ]
    assert AuditAction.LOGOUT.value in actions
